"""Private SQLite editorial records and append-only publication versions."""
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT, content_hash TEXT, record TEXT NOT NULL, PRIMARY KEY(id,content_hash));
        CREATE TABLE IF NOT EXISTS source_health(source_id TEXT PRIMARY KEY, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS editions(date TEXT PRIMARY KEY, current_run TEXT NOT NULL REFERENCES runs(id), state TEXT NOT NULL CHECK(state IN ('draft','needs_review','approved','published','corrected')), revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS versions(run_id TEXT PRIMARY KEY REFERENCES runs(id), date TEXT NOT NULL, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, date TEXT NOT NULL, run_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL);
        """)

    def save_run(self, run_id, record):
        self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)", (run_id, record["generated_at"], record["status"], json.dumps(record)))
        for e in record.get("all_evidence", []):
            self.db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?)", (e["id"], e["content_hash"], json.dumps(e)))
        for health in record.get("source_health", []):
            self.db.execute("INSERT OR REPLACE INTO source_health VALUES(?,?)", (health["source_id"], json.dumps(health)))
        self.db.commit()

    def save_edition(self, run_id, doc):
        old = self.db.execute("SELECT * FROM editions WHERE date=?", (doc["date"],)).fetchone()
        revision = old["revision"] + 1 if old else 1
        self.db.execute("INSERT INTO versions VALUES(?,?,?)", (run_id, doc["date"], json.dumps(doc)))
        self.db.execute("INSERT OR REPLACE INTO editions VALUES(?,?,?,?)", (doc["date"], run_id, "needs_review", revision))
        self._audit(doc["date"], run_id, "generator", "new_draft", "New generation requires fresh approval; previous approved versions remain in history.")
        self.db.commit()

    def get(self, date):
        row = self.db.execute("SELECT e.*,v.record FROM editions e JOIN versions v ON e.current_run=v.run_id WHERE e.date=?", (date,)).fetchone()
        if row is None:
            raise ValueError("No edition exists for this date")
        return dict(row), json.loads(row["record"])

    def list_editions(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM editions ORDER BY date DESC")]

    def transition(self, date, target, actor, note):
        row, doc = self.get(date)
        allowed = {"draft": {"needs_review"}, "needs_review": {"approved", "draft"}, "approved": {"needs_review"}, "published": {"corrected"}, "corrected": {"needs_review"}}
        if target not in allowed[row["state"]]:
            raise ValueError(f"Cannot move {row['state']} to {target}")
        if not actor.strip() or not note.strip():
            raise ValueError("An editor name and review note are required")
        if target == "approved" and not doc["stories"]:
            raise ValueError("An empty edition cannot be approved")
        self.db.execute("UPDATE editions SET state=? WHERE date=?", (target, date))
        self._audit(date, row["current_run"], actor, target, note)
        self.db.commit()

    def edited_version(self, date, run_id, doc, actor, note):
        row, _ = self.get(date)
        doc["status"] = "editorial_draft"
        self.save_run(run_id, doc)
        self.save_edition(run_id, doc)
        self._audit(date, run_id, actor, "edit", note)
        self.db.commit()

    def audit(self, date):
        return [dict(r) for r in self.db.execute("SELECT * FROM audit WHERE date=? ORDER BY id", (date,))]

    def _audit(self, date, run_id, actor, action, note):
        self.db.execute("INSERT INTO audit(date,run_id,at,actor,action,note) VALUES(?,?,?,?,?,?)", (date, run_id, datetime.now(timezone.utc).isoformat(), actor, action, note))

    def backup(self, target: Path):
        if target.exists():
            raise ValueError("Backup destination exists; choose a new path")
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target)
        self.db.backup(conn)
        conn.close()

    def close(self):
        self.db.close()
