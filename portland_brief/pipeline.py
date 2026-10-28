from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from .fetching import Fetcher
from .collection import NewsBuffer
from .selection import select
from .sources import collect, source_enabled
from .storage import Store
from .summarization import summarize
from .validation import numbers, suspicious
from .weather import get_weather
from .rendering import write_draft


@contextmanager
def run_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another draft or editorial write is in progress") from None
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def run(root, settings, registry, force=False, now=None, summarizer=summarize):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("An explicit time must include an offset")
    now = now.astimezone(timezone.utc)
    date = now.astimezone(ZoneInfo(settings["timezone"])).date().isoformat()
    run_id = now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    data = root / settings["data_path"]
    with run_lock(data / "run.lock"):
        run_dir = data / "runs" / run_id
        run_dir.mkdir(parents=True)
        store = Store(data / "editorial.sqlite3")
        doc = {"title": settings["title"], "date": date, "generated_at": datetime.now(timezone.utc).isoformat(), "as_of": now.isoformat(), "timezone": settings["timezone"], "window_hours": settings["window_hours"], "status": "running", "run_id": run_id, "stories": [], "gaps": [], "evidence": {}, "usage": {"provider": settings["provider"], "model": settings["model"], "input_tokens": 0, "output_tokens": 0, "attempts": 0, "api_cost_usd": 0.0}, "generator_version": "0.1.0", "public_publishing": False, "scheduling": settings.get('scheduling', False)}
        try:
            fetcher = Fetcher(root, settings, force)
            from .civic.store import CivicStore
            from .civic.development_reports import approved_watch, source_checked_watch
            civic = CivicStore(data / 'civic.sqlite3')
            try:
                tasks_path=root/'config/tasks.json'
                policy=json.loads(tasks_path.read_text()).get('filings',{}).get('draft_review_policy','approved_only') if tasks_path.exists() else 'approved_only'
                watch=source_checked_watch if policy=='automatic_source_checked' else approved_watch
                doc['development_watch'] = watch(civic, now, settings['window_hours'], min(3, settings['story_limit']))
                if settings.get('government_watch', {}).get('enabled'):
                    from .civic.government_watch import snapshot, names
                    doc['government_watch'] = snapshot(civic,now,settings['government_watch'])
                    settings = {**settings, 'government_names':names(civic,now)}
                    doc['gaps'] += doc['government_watch']['gaps']
            finally:
                civic.close()
            items, health = collect(fetcher, registry, settings)
            doc.update(source_health=health, all_evidence=[x.to_dict() for x in items])
            failures = [h for h in health if h["status"] in {"failed", "stale_cache"}]
            doc["gaps"] += [f"{h['name']}: live source unavailable; excluded from this brief ({h.get('error', 'failure')})." for h in failures]
            doc["gaps"] += [f"{h['name']}: {h.get('coverage_note', 'disabled pending source/access validation')}." for h in health if h["status"] == "disabled"]
            if not any(h["status"] in {"ok", "cached", "not_modified"} for h in health):
                raise RuntimeError("All news sources failed; previous successful draft preserved")
            buffer = NewsBuffer(data / "news-collection.sqlite3")
            try:
                buffer.record(items, health, now)
                buffered, late_ids = buffer.candidates(now, date, settings["window_hours"], settings.get("late_arrival_lookback_hours", 72))
                previously_used = buffer.used_before(items, date)
            finally:
                buffer.close()
            enabled_ids = {s['id'] for s in registry if source_enabled(s)}
            buffered = [i for i in buffered if i.source_id in enabled_ids]
            late_ids = [eid for eid in late_ids if any(i.id == eid for i in buffered)]
            # Keep undated/current invalid items for the existing explicit exclusion accounting.
            items_by_id = {i.id: i for i in items if i.id not in previously_used}
            items_by_id.update({i.id: i for i in buffered})
            items = list(items_by_id.values())
            doc["late_arrival_ids"] = late_ids
            clean = [i for i in items if not suspicious(i.title + " " + i.text)]
            if len(clean) != len(items):
                doc["gaps"].append(f"{len(items) - len(clean)} source item(s) quarantined for embedded instructions.")
            # A few additional existing candidates can replace rejected summaries.
            chosen, selection = select(clean, now, {**settings, "late_arrival_ids": late_ids, "story_limit": settings["story_limit"] + 4})
            doc["selection"] = selection
            insufficient = selection["excluded"].get("insufficient_evidence", 0)
            if insufficient:
                doc["gaps"].append(f"{insufficient} recent local item(s) lacked enough descriptive evidence and were omitted.")
            if late_ids:
                # Older items are admitted only when collection proves they arrived late.
                chosen = [i for i in chosen if i.id in late_ids or not i.published_at or (now - datetime.fromisoformat(i.published_at)).total_seconds() <= settings["window_hours"] * 3600]
                doc["gaps"].append(f"{len(late_ids)} late-arriving item(s) considered outside the normal publication window; publication times remain visible.")
            doc["weather"] = get_weather(fetcher, settings, now)
            doc["gaps"] += doc["weather"]["gaps"]
            doc["evidence"] = {i.id: i.to_dict() for i in chosen}
            if chosen:
                stories, rejected, usage = summarizer(chosen, settings, root, run_dir)
                doc["usage"], doc["model_failures"] = usage, rejected
                if rejected:
                    doc["gaps"].append(f"{len(rejected)} selected story summary/summaries failed validation; omitted rather than guessed.")
                if not stories:
                    raise RuntimeError("No model summaries passed evidence validation; previous draft preserved")
                by_id = {i.id: i for i in items}
                if settings.get('government_watch', {}).get('enabled'):
                    from .government import order_stories
                    stories = order_stories(stories,by_id,settings)
                for story in stories:
                    eid = story.citation_ids[0]
                    related = selection["overlapping_evidence_ids"].get(eid, [])
                    if any(numbers(by_id[eid].text) != numbers(by_id[x].text) for x in related):
                        story.flags.append("overlapping_coverage_check")
                doc["stories"] = [s.to_dict() for s in stories[:max(0, settings['story_limit']-len(doc['development_watch']))]]
            doc["status"] = "partial" if doc["gaps"] else "draft"
            store.save_run(run_id, doc)
            store.save_edition(run_id, doc)
            path = write_draft(root, settings, doc, run_id)
            (run_dir / "run.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
            buffer = NewsBuffer(data / "news-collection.sqlite3")
            try:
                buffer.mark_generated(doc["stories"], doc["evidence"], date, now)
            finally:
                buffer.close()
            return doc, path
        except Exception as exc:
            doc.update(status="failed", error=str(exc))
            store.save_run(run_id, doc)
            (run_dir / "run.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
            raise
        finally:
            store.close()
