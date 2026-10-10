"""Durable local scheduler; independent job failures, LA dates and bounded retries.

No OS service is installed by importing or running status. `tick --run` is explicit
manual execution; `serve` starts a foreground scheduler only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from .pipeline import run_lock


def next_local_daily(now, clock='07:00', zone='America/Los_Angeles'):
    local = now.astimezone(ZoneInfo(zone))
    hour, minute = map(int, clock.split(':'))
    for offset in range(3):
        day = local.date() + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(zone))
        utc = candidate.astimezone(timezone.utc)
        # Skip a nonexistent spring-forward time rather than inventing an offset.
        if utc.astimezone(ZoneInfo(zone)).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if utc > now.astimezone(timezone.utc):
            return utc
    raise ValueError('No valid upcoming local daily time')


def definitions(root):
    config = json.loads((root / 'config/tasks.json').read_text())
    python = str(root / '.venv/bin/python')
    jobs = {
        'news-collect': ('news', [python, '-m', 'portland_brief.cli', '--root', str(root), 'collect-news'], config['schedule']['news_collection_minutes'] * 60),
        'news-synthesis': ('news', [python, '-m', 'portland_brief.cli', '--root', str(root), 'run'], None),
        'meetings-discovery': ('meetings', [python, '-m', 'portland_brief.civic.cli', '--root', str(root), 'discover'], config['schedule']['meeting_discovery_minutes'] * 60),
        'filings-collect': ('filings', [python, '-m', 'portland_brief.civic.cli', '--root', str(root), 'filings'], config['schedule']['filing_collection_minutes'] * 60)
    }
    if config.get('repository_archive', {}).get('enabled'):
        jobs['archive-sync'] = ('archive', [python, '-m', 'portland_brief.repository_archive', '--root', str(root), '--push'], 300)
    return config, jobs


def initial_daily(root, now, config):
    """Catch up today's missing edition after wake/login; never fabricate backfill."""
    local = now.astimezone(ZoneInfo(config['timezone']))
    hour, minute = map(int, config['daily_brief']['daily_time'].split(':'))
    missing = True
    path = root / 'data/editorial.sqlite3'
    if path.exists():
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            missing = not db.execute('SELECT 1 FROM editions WHERE date=?', (local.date().isoformat(),)).fetchone()
    if config['schedule'].get('catch_up_after_wake', True) and missing and (local.hour, local.minute) >= (hour, minute):
        return now
    return next_local_daily(now, config['daily_brief']['daily_time'], config['timezone'])


def tick(root, execute=False, now=None, runner=subprocess.run):
    now = now or datetime.now(timezone.utc)
    config, jobs = definitions(root)
    folder = root / 'data/scheduler'; folder.mkdir(parents=True, exist_ok=True)
    output = []
    with run_lock(folder / 'scheduler.lock'):
        db = sqlite3.connect(folder / 'jobs.sqlite3')
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,pipeline TEXT,next_due TEXT,state TEXT,attempts INTEGER,last_result TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,job_id TEXT,started_at TEXT,finished_at TEXT,exit_code INTEGER,log_path TEXT);''')
        for key, (pipeline, command, cadence) in jobs.items():
            initial = now if cadence else initial_daily(root, now, config)
            db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,\'pending\',0,NULL)', (key, pipeline, initial.isoformat()))
        # A crashed scheduler leaves running entries retryable on its next exclusive tick.
        db.execute("UPDATE jobs SET state='retry' WHERE state='running'")
        # Manual generation can finish while a scheduled occurrence is retrying.
        # Reuse that dated edition rather than invalidating it with another run.
        daily = db.execute("SELECT next_due FROM jobs WHERE id='news-synthesis'").fetchone()
        edition_db = root / 'data/editorial.sqlite3'
        if daily['next_due'] <= now.isoformat() and edition_db.exists():
            with sqlite3.connect(f'file:{edition_db}?mode=ro', uri=True) as editions:
                today = now.astimezone(ZoneInfo(config['timezone'])).date().isoformat()
                existing = editions.execute('SELECT 1 FROM editions WHERE date=?', (today,)).fetchone()
            if existing:
                db.execute("UPDATE jobs SET next_due=?,state='pending',attempts=0,last_result=? WHERE id='news-synthesis'", (next_local_daily(now, config['daily_brief']['daily_time'], config['timezone']).isoformat(), json.dumps({'reused_existing_edition': today})))
        due = [dict(r) for r in db.execute('SELECT * FROM jobs WHERE next_due<=? AND state IN (\'pending\',\'retry\')', (now.isoformat(),))]
        if not execute:
            result = {'due_jobs': due, 'next_daily': db.execute("SELECT next_due FROM jobs WHERE id='news-synthesis'").fetchone()[0], 'executed': False, 'os_service_installed': config['schedule']['installed']}
            db.commit(); db.close(); return result
        # Persist claims before launching children. One failed job does not cancel other pipelines.
        for job in due:
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1 WHERE id=?", (job['id'],))
        db.commit()
        def run_job(job):
            _, command, _ = jobs[job['id']]
            path = folder / (job['id'] + '-' + now.strftime('%Y%m%dT%H%M%S') + '.log')
            try:
                with path.open('w') as log:
                    result = runner(command, stdout=log, stderr=subprocess.STDOUT, timeout=900, cwd=root)
                return job, result.returncode, path
            except (OSError, subprocess.SubprocessError) as exc:
                path.write_text(type(exc).__name__ + ': job failed or timed out')
                return job, 1, path
        # Source parsing and collection can run concurrently; daily model generation remains bounded.
        with ThreadPoolExecutor(max_workers=3) as pool:
            for job, code, log in pool.map(run_job, due):
                attempts = job['attempts'] + 1
                cadence = jobs[job['id']][2]
                state = 'retry' if code and attempts < 3 else 'pending'
                next_due = now + timedelta(seconds=min(300 * attempts, 900)) if state == 'retry' else now + timedelta(seconds=cadence) if cadence else next_local_daily(now, config['daily_brief']['daily_time'], config['timezone'])
                db.execute('UPDATE jobs SET state=?,next_due=?,attempts=?,last_result=? WHERE id=?', (state, next_due.isoformat(), attempts if state == 'retry' else 0, json.dumps({'exit_code': code, 'log': str(log)}), job['id']))
                db.execute('INSERT INTO attempts(job_id,started_at,finished_at,exit_code,log_path) VALUES(?,?,?,?,?)', (job['id'], now.isoformat(), datetime.now(timezone.utc).isoformat(), code, str(log)))
                output.append({'job': job['id'], 'exit_code': code, 'state': state, 'next_due': next_due.isoformat(), 'log': str(log)})
                db.commit()
        db.close()
    return {'results': output, 'executed': True, 'website_published': False, 'os_service_installed': config['schedule']['installed']}


def main(argv=None):
    p = argparse.ArgumentParser(description='Independent local collectors and configured repository draft archive')
    p.add_argument('--root', type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest='command', required=True)
    one = sub.add_parser('tick'); one.add_argument('--run', action='store_true')
    one.add_argument('--scheduled', action='store_true', help='Respect the configured pause switch (for launchd)')
    serve = sub.add_parser('serve'); serve.add_argument('--poll-seconds', type=int, default=30)
    args = p.parse_args(argv)
    root = args.root.resolve()
    if args.command == 'tick':
        if args.scheduled and not definitions(root)[0]['schedule'].get('enabled'):
            print(json.dumps({'executed': False, 'paused': True})); return 0
        print(json.dumps(tick(root, args.run), indent=2)); return 0
    if not 1 <= args.poll_seconds <= 60:
        p.error('Poll interval must be 1–60 seconds')
    try:
        while True:
            if definitions(root)[0]['schedule'].get('enabled'):
                print(json.dumps(tick(root, True)), flush=True)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
