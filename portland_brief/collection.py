"""News checkpoints independent of synthesis; bounded late-arrival retention."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from .records import Evidence, parse_time


class NewsBuffer:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS collected(id TEXT NOT NULL,content_hash TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(id,content_hash));
        CREATE TABLE IF NOT EXISTS checkpoints(source_id TEXT PRIMARY KEY,at TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS consumed(id TEXT NOT NULL,content_hash TEXT NOT NULL,date TEXT NOT NULL,PRIMARY KEY(id,content_hash,date));
        CREATE TABLE IF NOT EXISTS syntheses(date TEXT PRIMARY KEY,completed_at TEXT NOT NULL);
        ''')

    def record(self, items, health, now):
        stamp = now.isoformat()
        for item in items:
            self.db.execute('INSERT INTO collected VALUES(?,?,?,?,?) ON CONFLICT(id,content_hash) DO UPDATE SET last_seen=excluded.last_seen,record=excluded.record', (item.id, item.content_hash, stamp, stamp, json.dumps(item.to_dict())))
        for source in health:
            if source['status'] in {'ok','cached','not_modified'}:
                self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)', (source['source_id'], stamp, source['status'], json.dumps(source)))
        self.db.commit()

    def candidates(self, now, date, window_hours=24, lookback_hours=72):
        previous = self.db.execute('SELECT MAX(completed_at) FROM syntheses WHERE date<?', (date,)).fetchone()[0]
        cutoff = datetime.fromisoformat(previous) if previous else now - timedelta(hours=window_hours)
        result, late = [], []
        for row in self.db.execute('SELECT * FROM collected WHERE last_seen>=?', ((now-timedelta(hours=lookback_hours)).isoformat(),)):
            item = Evidence(**json.loads(row['record']))
            published = parse_time(item.published_at)
            used_other_date = self.db.execute('SELECT 1 FROM consumed WHERE id=? AND content_hash=? AND date<?', (item.id, item.content_hash, date)).fetchone()
            if used_other_date:
                continue
            if published and now - timedelta(hours=window_hours) <= published <= now:
                result.append(item)
            elif published and now-timedelta(hours=lookback_hours) <= published <= now and datetime.fromisoformat(row['first_seen']) > cutoff:
                result.append(replace(item, context=item.context + '; late-arriving publication outside the ordinary daily window'))
                late.append(item.id)
        return result, late

    def used_before(self, items, date):
        return {i.id for i in items if self.db.execute('SELECT 1 FROM consumed WHERE id=? AND content_hash=? AND date<?', (i.id, i.content_hash, date)).fetchone()}

    def mark_generated(self, stories, evidence, date, now):
        for story in stories:
            for eid in story['citation_ids']:
                self.db.execute('INSERT OR IGNORE INTO consumed VALUES(?,?,?)', (eid, evidence[eid]['content_hash'], date))
        self.db.execute('INSERT OR REPLACE INTO syntheses VALUES(?,?)', (date, now.isoformat()))
        self.db.commit()

    def close(self):
        self.db.close()
