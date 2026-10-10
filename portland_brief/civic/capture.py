"""Recoverable input recording ledger. No speech generation or distribution."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from ..pipeline import run_lock
from ..records import safe_url
from .store import stable_id, stamp

ALLOWED_MEDIA = {'archive-video.granicus.com', 'multnomah.granicus.com', 'www.portland.gov', 'portland.gov'}


def create_job(store, meeting_id, source_url, mode='archived'):
    if mode not in {'live', 'archived', 'local'}:
        raise ValueError('Unknown capture mode')
    if not store.db.execute('SELECT 1 FROM meetings WHERE id=?', (meeting_id,)).fetchone():
        raise ValueError('Unknown meeting')
    if mode != 'local':
        safe_url(source_url, list(ALLOWED_MEDIA))
    jid = stable_id('capture', meeting_id, source_url, mode)
    if not store.db.execute('SELECT 1 FROM capture_jobs WHERE id=?', (jid,)).fetchone():
        store.upsert('capture_jobs', id=jid, meeting_id=meeting_id, source_url=source_url, mode=mode, state='pending', data={'attempts': 0, 'complete': False, 'missing_intervals': [], 'audio_minutes': 0, 'storage_bytes': 0, 'input_tokens': 0, 'output_tokens': 0})
    store.commit()
    return jid


def save_segment(store, job_id, path, start_seconds, end_seconds):
    path = Path(path)
    if start_seconds < 0 or end_seconds <= start_seconds or not path.is_file():
        raise ValueError('Invalid audio interval or missing artifact')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    sid = stable_id('capture-segment', job_id, start_seconds, end_seconds, sha)
    store.upsert('capture_segments', id=sid, job_id=job_id, start_seconds=start_seconds, end_seconds=end_seconds, artifact_path=str(path), content_hash=sha, data={'storage_bytes': path.stat().st_size})
    store.commit()
    return sid


def gap(store, job_id, start, end, reason):
    if start < 0 or (end is not None and end < start):
        raise ValueError('Invalid missing interval')
    store.upsert('capture_gaps', id=stable_id('capture-gap', job_id, start, end, reason), job_id=job_id, start_seconds=start, end_seconds=end, reason=reason, data={'resolved': False})
    store.commit()


def finish(store, job_id, evidence_of_completion=None):
    job = store.db.execute('SELECT * FROM capture_jobs WHERE id=?', (job_id,)).fetchone()
    if not job:
        raise ValueError('Unknown capture')
    segments = store.db.execute('SELECT * FROM capture_segments WHERE job_id=? ORDER BY start_seconds', (job_id,)).fetchall()
    gaps = store.db.execute('SELECT * FROM capture_gaps WHERE job_id=?', (job_id,)).fetchall()
    ranges = []
    for segment in segments:
        if ranges and segment['start_seconds'] <= ranges[-1][1] + .5:
            ranges[-1][1] = max(ranges[-1][1], segment['end_seconds'])
        else:
            ranges.append([segment['start_seconds'], segment['end_seconds']])
    missing = [{'start_seconds': ranges[i - 1][1], 'end_seconds': ranges[i][0]} for i in range(1, len(ranges))]
    if ranges and ranges[0][0] > .5:
        missing.insert(0, {'start_seconds': 0, 'end_seconds': ranges[0][0]})
    missing += [dict(g) for g in gaps if not json.loads(g['data']).get('resolved')]
    # EOF alone is never evidence that a live meeting ended. An archived asset only proves its own completeness.
    complete = bool(evidence_of_completion) and bool(segments) and not missing
    data = {**json.loads(job['data']), 'complete': complete, 'completion_evidence': evidence_of_completion, 'completeness_scope': 'archived_asset' if job['mode'] == 'archived' else 'capture_only', 'missing_intervals': missing, 'audio_minutes': sum(b-a for a, b in ranges) / 60, 'storage_bytes': sum(json.loads(s['data'])['storage_bytes'] for s in segments)}
    store.upsert('capture_jobs', **{**dict(job), 'state': 'captured' if complete else 'interrupted', 'data': data})
    store.commit()
    return data


def run_capture(store, job_id, maximum_seconds=3600, retry_limit=2):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('ffmpeg and ffprobe are required for input capture')
    job = store.db.execute('SELECT * FROM capture_jobs WHERE id=?', (job_id,)).fetchone()
    if not job:
        raise ValueError('Unknown capture')
    if maximum_seconds <= 0 or retry_limit not in range(1, 6):
        raise ValueError('Bound capture duration and retries')
    if job['mode'] != 'local':
        safe_url(job['source_url'], list(ALLOWED_MEDIA))
    dest = store.path.parent / 'capture' / job_id.replace(':', '-')
    dest.mkdir(parents=True, exist_ok=True)
    with run_lock(dest / 'capture.lock'):
        metadata = json.loads(job['data'])
        if metadata.get('complete'):
            return metadata
        # Each run retains its chunks. Do not stitch a reconnect into a false continuous timeline.
        existing = store.db.execute('SELECT MAX(end_seconds) FROM capture_segments WHERE job_id=?', (job_id,)).fetchone()[0] or 0
        offset = existing
        capture_started = time.monotonic()
        for attempt in range(retry_limit):
            remaining = maximum_seconds - (time.monotonic() - capture_started)
            if remaining <= 0:
                break
            attempt_dir = dest / ('attempt-' + str(metadata.get('attempts', 0) + 1))
            attempt_dir.mkdir(exist_ok=True)
            metadata['attempts'] = metadata.get('attempts', 0) + 1
            store.upsert('capture_jobs', **{**dict(job), 'state': 'capturing', 'data': metadata})
            store.commit()
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error']
            if job['mode'] != 'live' and offset:
                command += ['-ss', str(offset)]
            # Restrict protocols; source content cannot open local files or arbitrary ffmpeg handlers.
            if job['mode'] != 'local':
                command += ['-protocol_whitelist', 'https,http,tls,tcp,crypto', '-rw_timeout', '20000000']
            command += ['-i', job['source_url'], '-t', str(remaining), '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-f', 'segment', '-segment_time', '30', '-reset_timestamps', '1', str(attempt_dir / '%06d.wav')]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            deadline, saved, cursor = time.monotonic() + remaining + 40, set(), offset
            def persist(files):
                nonlocal cursor
                for file in files:
                    if file in saved:
                        continue
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(file)], capture_output=True, text=True, timeout=15)
                    duration = float(json.loads(probe.stdout)['format']['duration'])
                    save_segment(store, job_id, file, cursor, cursor + duration)
                    saved.add(file)
                    cursor += duration
            while process.poll() is None and time.monotonic() < deadline:
                files = sorted(attempt_dir.glob('*.wav'))
                persist(files[:-1])  # last chunk may still be written
                time.sleep(1)
            timed_out = process.poll() is None
            if timed_out:
                process.terminate()
            try:
                _, error = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _, error = process.communicate()
            try:
                persist(sorted(attempt_dir.glob('*.wav')))
            except (ValueError, KeyError, subprocess.SubprocessError):
                pass
            metadata['last_error'] = error.decode(errors='replace')[-500:]
            store.upsert('capture_jobs', **{**dict(job), 'state': 'interrupted', 'data': metadata})
            store.commit()
            # A configured duration ceiling does not establish EOF. Check asset duration for archived/local inputs.
            asset_duration = None
            if job['mode'] != 'live':
                try:
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', job['source_url']], capture_output=True, text=True, timeout=30)
                    asset_duration = float(json.loads(probe.stdout)['format']['duration'])
                except (ValueError, KeyError, subprocess.SubprocessError):
                    pass
            if process.returncode == 0 and asset_duration is not None and cursor >= asset_duration - .5:
                return finish(store, job_id, {'asset_duration_seconds': asset_duration, 'source_url': job['source_url'], 'verified_asset_end': True})
            if job['mode'] == 'live':
                if attempt + 1 < retry_limit and time.monotonic() - capture_started < maximum_seconds:
                    time.sleep(min(2 ** attempt, 4))
                    resumed = max(cursor, existing + time.monotonic() - capture_started)
                    gap(store, job_id, cursor, resumed, 'stream disconnect/recess; capture-relative interval and alignment require review')
                    offset = resumed
                    continue
                gap(store, job_id, cursor, None, 'stream ended or disconnected; meeting completion unverified')
                return finish(store, job_id)
            offset = cursor
        return finish(store, job_id)


def import_transcript(store, meeting_id, payload, source_url, job_id=None):
    segments = payload['segments']
    automatic = payload.get('automatic', True)
    if not store.db.execute('SELECT 1 FROM meetings WHERE id=?', (meeting_id,)).fetchone():
        raise ValueError('Unknown meeting')
    for segment in segments:
        if not isinstance(segment.get('text'), str) or segment['start_seconds'] < 0 or segment['end_seconds'] <= segment['start_seconds']:
            raise ValueError('Transcript segments require text and bounded audio timestamps')
    raw = json.dumps(payload, sort_keys=True).encode()
    version, changed = store.source_version(source_url, raw, 'timestamped-transcript-v1', payload)
    for segment in segments:
        sid = stable_id('transcript-segment', meeting_id, version, segment['start_seconds'], segment['end_seconds'])
        # Diarization labels are stored raw. Name assignments require a later explicit recording review.
        store.upsert('transcript_segments', id=sid, job_id=job_id, meeting_id=meeting_id, start_seconds=segment['start_seconds'], end_seconds=segment['end_seconds'], raw_text=segment['text'], raw_speaker=segment.get('speaker'), corrected_text=None, person_id=None, attribution_status='uncertain', data={'automatic': automatic, 'source_version': version, 'review_required': True})
        passage = store.passage(version, sid, segment['text'], 'automatic_transcript' if automatic else 'transcript_unverified', segment['start_seconds'], segment['end_seconds'])
        store.fact('transcript_segment', sid, 'raw_transcript', segment['text'], passage, 'provisional')
    store.commit()
    return {'segments': len(segments), 'source_version': version, 'changed': changed, 'attribution': 'uncertain', 'outcomes': 'provisional_until_reconciled'}


def vocabulary(store):
    words = {r['name'] for r in store.rows('people')} | {r['name'] for r in store.rows('bodies')}
    for appearance in store.rows('appearances'):
        data = json.loads(appearance['data'])
        words.update(x for x in [data.get('responsible_department'), data.get('neighborhood_fields'), data.get('title')] if x)
    return sorted(words)


def segment_by_agenda(segments, max_characters=12000):
    """Bound extraction batches; retain timestamps, IDs and explicit agenda IDs."""
    if max_characters < 100:
        raise ValueError('Context allowance too small')
    batches, current, size, agenda = [], [], 0, None
    for segment in segments:
        length = len(segment['text'])
        if length > max_characters:
            raise ValueError('A segment exceeds context allowance; split it while retaining timestamps')
        if current and (size + length > max_characters or segment.get('agenda_id') != agenda):
            batches.append(current)
            current, size = [], 0
        agenda = segment.get('agenda_id')
        current.append(segment)
        size += length
    if current:
        batches.append(current)
    return batches
