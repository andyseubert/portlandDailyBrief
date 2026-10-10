import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .config import load_config
from .collection import NewsBuffer
from .editorial import apply_edits, export_approved, review_page
from .fetching import Fetcher
from .pipeline import run, run_lock
from .sources import collect
from .storage import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private, text-only Portland news drafts")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project directory with config/")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("run", help="Fetch live evidence, summarize locally and write a draft")
    generate.add_argument("--refresh", action="store_true", help="Revalidate sources even within cache TTL")
    sources = sub.add_parser("validate-sources", help="Fetch and validate enabled source formats; no generation")
    sources.add_argument("--refresh", action="store_true")
    sub.add_parser("collect-news", help="Collect source snippets/checkpoints without model generation")
    sub.add_parser("status", help="List private editions and source health")
    review = sub.add_parser("review", help="Write a private evidence/review HTML page")
    review.add_argument("date")
    edits = sub.add_parser("export-edits", help="Export editable summaries; remove rows to reject stories")
    edits.add_argument("date")
    edits.add_argument("--out", type=Path, required=True)
    apply = sub.add_parser("apply-edits", help="Validate edits, save a new version and invalidate approval")
    apply.add_argument("date")
    apply.add_argument("--file", type=Path, required=True)
    apply.add_argument("--editor", required=True)
    apply.add_argument("--note", required=True)
    for name in ["approve", "reject"]:
        p = sub.add_parser(name, help="Record explicit editorial review; never publish")
        p.add_argument("date")
        p.add_argument("--editor", required=True)
        p.add_argument("--note", required=True)
    unapprove = sub.add_parser("reopen", help="Move an approved edition back to needs_review")
    unapprove.add_argument("date")
    unapprove.add_argument("--editor", required=True)
    unapprove.add_argument("--note", required=True)
    export = sub.add_parser("build-approved", help="Export only approved summaries to a NEW local folder")
    export.add_argument("--out", type=Path, required=True)
    backup = sub.add_parser("backup", help="Create a consistent SQLite snapshot without overwriting")
    backup.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    try:
        settings, registry = load_config(root)
        if args.command == "run":
            doc, path = run(root, settings, registry, force=args.refresh)
            print(json.dumps({"status": doc["status"], "stories": len(doc["stories"]), "markdown": str(path / "brief.md"), "html": str(path / "brief.html"), "coverage_gaps": doc["gaps"], "usage": doc["usage"], "published": False, "scheduled": settings.get('scheduling', False)}, indent=2))
            return 0
        if args.command == "collect-news":
            with run_lock(root / settings["data_path"] / "news-collection.lock"):
                items, health = collect(Fetcher(root, settings), registry, settings)
                buffer = NewsBuffer(root / settings["data_path"] / "news-collection.sqlite3")
                try:
                    buffer.record(items, health, datetime.now(timezone.utc))
                finally:
                    buffer.close()
                print(json.dumps({"collected": len(items), "source_health": health, "model_tokens": 0}, indent=2))
                return 1 if not any(h["status"] in {"ok", "cached", "not_modified"} for h in health) else 0
        if args.command == "validate-sources":
            _, health = collect(Fetcher(root, settings, args.refresh), registry, settings)
            report = root / settings["data_path"] / "source-health.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(health, indent=2))
            by_id = {h["source_id"]: h for h in health}
            for source in registry:
                h = by_id[source["id"]]
                if h["status"] in {"ok", "cached", "not_modified"}:
                    source["last_validation_date"] = h["last_validation_date"]
                source["health_status"] = h["status"]
            (root / "config/sources.json").write_text(json.dumps(registry, indent=2) + "\n")
            print(json.dumps(health, indent=2))
            return 1 if any(h["status"] in {"failed", "stale_cache"} for h in health) else 0
        if args.command == "apply-edits":
            print(apply_edits(root, settings, args.date, args.file, args.editor, args.note))
            return 0
        with run_lock(root / settings["data_path"] / "run.lock"):
            store = Store(root / settings["data_path"] / "editorial.sqlite3")
            try:
                if args.command == "status":
                    print(json.dumps({"editions": store.list_editions(), "source_health": [json.loads(r[0]) for r in store.db.execute("SELECT record FROM source_health")], "published": False, "scheduled": settings.get('scheduling', False)}, indent=2))
                elif args.command == "review":
                    print(review_page(store, args.date, root / settings["output_path"] / "private-review" / (args.date + ".html")))
                elif args.command == "export-edits":
                    row, doc = store.get(args.date)
                    payload = {"base_run_id": row["current_run"], "stories": [{k: v for k, v in s.items() if k != "flags"} for s in doc["stories"]]}
                    if args.out.exists():
                        raise ValueError("Edits destination exists; choose a new path")
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
                    print(args.out)
                elif args.command in {"approve", "reject", "reopen"}:
                    target = {"approve": "approved", "reject": "draft", "reopen": "needs_review"}[args.command]
                    store.transition(args.date, target, args.editor, args.note)
                    print(f"{args.date}: {target}. Nothing published.")
                elif args.command == "build-approved":
                    target = args.out.expanduser().resolve()
                    # Prevent placing approved output inside private data/draft trees.
                    for private in [root / settings["data_path"], root / settings["output_path"] / "drafts", root / settings["output_path"] / "private-review"]:
                        if target == private or private in target.parents or target in private.parents:
                            raise ValueError("Approved export must be separate from private data and drafts")
                    print(f"Exported {export_approved(store, target)} approved edition(s) locally. Nothing deployed.")
                elif args.command == "backup":
                    store.backup(args.out)
                    print(args.out)
            finally:
                store.close()
        return 0
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
