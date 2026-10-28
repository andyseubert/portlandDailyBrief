from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def stable_id(namespace, *values):
    content = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str)
    return namespace + ":" + hashlib.sha256(content.encode()).hexdigest()[:24]


def stamp():
    return datetime.now(timezone.utc).isoformat()


class IdentifierConflict(ValueError):
    """Explicit identifiers disagree; retain evidence without merging identities."""


class CivicStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(Path(__file__).with_name("schema.sql").read_text())
        self._tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._columns = {table: {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')} for table in self._tables}

    def upsert(self, table, **record):
        if table not in self._tables or "id" not in self._columns[table] or not set(record).issubset(self._columns[table]):
            raise ValueError("Unknown civic record fields")
        if isinstance(record.get("data"), dict):
            record["data"] = json.dumps(record["data"], sort_keys=True, ensure_ascii=False)
        keys = list(record)
        changes = ",".join(f"{k}=excluded.{k}" for k in keys if k != "id")
        sql = f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)}) ON CONFLICT(id) DO UPDATE SET {changes}"
        self.db.execute(sql, tuple(record[k] for k in keys))

    def rows(self, table):
        if table not in self._tables:
            raise ValueError("Unknown table")
        return [dict(r) for r in self.db.execute(f"SELECT * FROM {table}")]

    def source_version(self, url, data: bytes, parser_version, meaningful, pipeline="meetings", retrieved_at=None):
        raw_hash = hashlib.sha256(data).hexdigest()
        meaningful_hash = hashlib.sha256(json.dumps(meaningful, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        version_id = stable_id("source", url, raw_hash, parser_version)
        existing = self.db.execute('SELECT meaningful_hash FROM source_versions WHERE id=?', (version_id,)).fetchone()
        if existing and existing[0] != meaningful_hash:
            raise ValueError('Changed extraction for unchanged source bytes requires a new parser version')
        path = self.path.parent / "source-versions" / raw_hash
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        old = self.db.execute("SELECT * FROM source_current WHERE url=?", (url,)).fetchone()
        self.db.execute("INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?)", (version_id, url, retrieved_at or stamp(), raw_hash, parser_version, meaningful_hash, str(path)))
        self.db.execute("INSERT OR REPLACE INTO source_current VALUES(?,?,?)", (url, version_id, meaningful_hash))
        self.db.execute("INSERT INTO retrievals(pipeline,url,at,status,version_id) VALUES(?,?,?,?,?)", (pipeline, url, stamp(), "fetched_and_parsed", version_id))
        changed = old is not None and old["meaningful_hash"] != meaningful_hash
        if changed:
            self.upsert("changes", id=stable_id("change", url, old["version_id"], version_id), url=url, old_version=old["version_id"], new_version=version_id, kind="material_source_change", data={"review_required": True})
            # Material changes invalidate the specific reviewed evidence bundle.
            for report in self.rows("meeting_reports"):
                if not report["current_version"]:
                    continue
                bundle = self.db.execute("SELECT evidence_bundle FROM report_versions WHERE id=?", (report["current_version"],)).fetchone()
                if bundle and url in json.loads(bundle[0]):
                    self.db.execute("UPDATE meeting_reports SET state='needs_review' WHERE id=?", (report["id"],))
                    if report['state'] == 'approved':
                        self.db.execute('INSERT INTO report_audit(report_id,version_id,action,actor,at,review_seconds,note) VALUES(?,?,?,?,?,?,?)', (report['id'], report['current_version'], 'approval_invalidated', 'source_version_monitor', stamp(), 0, 'Material source change at ' + url))
            for report in self.rows('development_reports'):
                bundle = self.db.execute('SELECT evidence_bundle FROM development_report_versions WHERE id=?', (report['current_version'],)).fetchone()
                if bundle and url in json.loads(bundle[0]) and report['state'] == 'approved':
                    self.db.execute("UPDATE development_reports SET state='needs_review' WHERE id=?", (report['id'],))
                    self.db.execute('INSERT INTO development_review_audit(report_id,version_id,action,actor,at,review_seconds,note) VALUES(?,?,?,?,?,?,?)', (report['id'], report['current_version'], 'approval_invalidated', 'source_version_monitor', stamp(), 0, 'Material source change at ' + url))
        self.db.commit()
        return version_id, changed

    def failed_retrieval(self, pipeline, url, error):
        self.db.execute("INSERT INTO retrievals(pipeline,url,at,status,error) VALUES(?,?,?,?,?)", (pipeline, url, stamp(), "failed", str(error)[:500]))
        # Crucially: do not mutate source_current, matters, votes or removal status.
        self.db.commit()

    def passage(self, version_id, locator, text, evidence_kind="official_document", start_seconds=None, end_seconds=None):
        pid = stable_id("passage", version_id, locator, text, start_seconds, end_seconds)
        self.db.execute("INSERT OR IGNORE INTO passages VALUES(?,?,?,?,?,?,?)", (pid, version_id, locator, text, start_seconds, end_seconds, evidence_kind))
        return pid

    def fact(self, entity_type, entity_id, predicate, value, passage_id, status="official"):
        fid = stable_id("fact", entity_type, entity_id, predicate, value, passage_id)
        self.db.execute("INSERT OR IGNORE INTO facts VALUES(?,?,?,?,?,?,?)", (fid, entity_type, entity_id, predicate, json.dumps(value, sort_keys=True), passage_id, status))
        return fid

    def matter(self, jurisdiction_id, official_number, url, title, data):
        # Never use title similarity, agenda ordinal or adopted number as a matter ID.
        namespace, value = ("official_document_number", official_number) if official_number else ("source_url", url)
        row = self.db.execute("SELECT matter_id FROM matter_identifiers WHERE jurisdiction_id=? AND namespace=? AND value=?", (jurisdiction_id, namespace, value)).fetchone()
        mid = row[0] if row else stable_id("matter", jurisdiction_id, namespace, value)
        identifiers = [(namespace, value), ("source_url", url)]
        # Check before writing: failed_retrieval commits the failure audit, so a
        # conflict must not leave a new matter or overwrite an existing matter.
        for ns, val in identifiers:
            existing = self.db.execute("SELECT matter_id FROM matter_identifiers WHERE jurisdiction_id=? AND namespace=? AND value=?", (jurisdiction_id, ns, val)).fetchone()
            if existing and existing[0] != mid:
                raise IdentifierConflict(f"Conflicting official identifiers require manual reconciliation: {namespace}={value}; source_url={url}; existing_matter={existing[0]}; candidate_matter={mid}")
        old = self.db.execute("SELECT data FROM matters WHERE id=?", (mid,)).fetchone()
        merged = {**(json.loads(old[0]) if old else {}), **data}
        merged.setdefault("adoption_date", None)
        merged.setdefault("effective_date", None)
        merged.setdefault("implementation_status", "unknown")
        self.upsert("matters", id=mid, jurisdiction_id=jurisdiction_id, title=title, data=merged)
        for ns, val in identifiers:
            self.db.execute("INSERT OR IGNORE INTO matter_identifiers VALUES(?,?,?,?)", (jurisdiction_id, ns, val, mid))
        return mid

    def adopted_identifier(self, jurisdiction_id, mid, kind, number):
        old = self.db.execute('SELECT matter_id FROM matter_identifiers WHERE jurisdiction_id=? AND namespace=? AND value=?', (jurisdiction_id, kind + '_number', number)).fetchone()
        if old and old[0] != mid:
            raise ValueError('Conflicting adopted identifiers require review')
        self.db.execute("INSERT OR IGNORE INTO matter_identifiers VALUES(?,?,?,?)", (jurisdiction_id, kind + "_number", number, mid))

    def procedure(self, body_id, when):
        day = when[:10]
        rows = self.db.execute("SELECT * FROM procedure_versions WHERE body_id=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC", (body_id, day, day)).fetchall()
        return json.loads(rows[0]["data"]) if rows else None

    def membership(self, body_id, when):
        day = when[:10]
        return [dict(r) for r in self.db.execute("SELECT m.*,p.name FROM memberships m JOIN people p ON p.id=m.person_id WHERE m.body_id=? AND m.effective_from<=? AND (m.effective_to IS NULL OR m.effective_to>?)", (body_id, day, day))]

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.close()
