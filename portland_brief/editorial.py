"""Private evidence/review tools; approval is an explicit local editor action."""
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import uuid

from .pipeline import run_lock
from .records import Evidence
from .rendering import page, atomic_text, write_draft
from .storage import Store
from .validation import validate_story


def review_page(store, date, target):
    row, doc = store.get(date)
    e, blocks = escape, []
    blocks += [f"<h1>Private editorial review — {e(date)}</h1>", f"<p class=\"notice\">State: {e(row['state'])}. Revision {row['revision']}. Local only; review does not publish anything.</p>", "<p>Compare each claim with its evidence. These automatic checks establish citation structure and matching text spans; they do not establish factual entailment or completeness.</p>"]
    for s in doc["stories"]:
        blocks += ["<article><h2>" + e(s["headline"]) + "</h2>", "<p>" + e(" ".join(s["sentences"])) + "</p>", "<p>Flags: " + e(", ".join(s["flags"])) + "</p>"]
        for eid in s["citation_ids"]:
            record = doc["evidence"][eid]
            blocks += [f'<p><a href="{e(record["url"], quote=True)}">{e(record["source_name"])}</a> · {e(eid)}</p>', "<details open><summary>Fetched evidence</summary><p>" + e(record["title"]) + "</p><blockquote>" + e(record["text"]) + "</blockquote></details>", "<p class=\"meta\">Content hash: " + e(record["content_hash"]) + "</p>"]
        blocks += ["<ol>"] + ["<li>" + e(span) + "</li>" for span in s["evidence_spans"]] + ["</ol></article>"]
    blocks += ["<h2>Coverage gaps</h2><ul>"] + ["<li>" + e(g) + "</li>" for g in doc["gaps"]] + ["</ul><h2>Audit history</h2><ul>"]
    blocks += ["<li>" + e(f"{a['at']} · {a['actor']} · {a['action']}: {a['note']}") + "</li>" for a in store.audit(date)] + ["</ul>"]
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(target, page("Private editorial evidence", "".join(blocks)))
    return target


def apply_edits(root, settings, date, edit_path, actor, note):
    if not actor.strip() or not note.strip():
        raise ValueError("An editor name and note are required")
    with run_lock(root / settings["data_path"] / "run.lock"):
        store = Store(root / settings["data_path"] / "editorial.sqlite3")
        try:
            row, old = store.get(date)
            edits = json.loads(edit_path.read_text())
            if set(edits) != {"base_run_id", "stories"} or edits["base_run_id"] != row["current_run"]:
                raise ValueError("Edit is stale or invalid; export the current edition before editing")
            evidence = {eid: Evidence(**r) for eid, r in old["evidence"].items()}
            stories = []
            seen = set()
            for raw in edits["stories"]:
                ids = raw.get("citation_ids", [])
                if len(ids) != 1 or ids[0] not in evidence or ids[0] in seen:
                    raise ValueError("Invalid, unknown, or duplicate edit citation")
                stories.append(validate_story(raw, evidence[ids[0]]).to_dict())
                seen.add(ids[0])
            if not stories:
                raise ValueError("Edit cannot remove every story; reject the edition instead")
            doc = deepcopy(old)
            run_id = "edit-" + uuid.uuid4().hex[:16]
            doc.update(stories=stories, run_id=run_id, generated_at=datetime.now(timezone.utc).isoformat(), editor=actor, edit_note=note)
            store.edited_version(date, run_id, doc, actor, note)
            return write_draft(root, settings, doc, run_id)
        finally:
            store.close()


def export_approved(store, target):
    # This is a local export, not a deployment. Never read the draft output tree.
    if target.exists():
        raise ValueError("Export destination exists; choose a new directory to preserve prior builds")
    from .rendering import html, markdown
    approved = []
    for row in store.list_editions():
        if row["state"] == "approved":
            _, doc = store.get(row["date"])
            approved.append(doc)
    if not approved:
        raise ValueError("No editor-approved editions exist; no export created")
    target.mkdir(parents=True)
    archive = target / "archive"
    archive.mkdir()
    for doc in approved:
        folder = archive / doc["date"]
        folder.mkdir()
        # Export contains summaries and citations, never source snippets/model inputs.
        (folder / "brief.html").write_text(html(doc))
        (folder / "brief.md").write_text(markdown(doc))
    (target / "index.html").write_text(html(approved[0], root_index=True))
    links = "".join(f'<li><a href="{d["date"]}/brief.html">{d["date"]}</a></li>' for d in approved)
    (archive / "index.html").write_text(page("Approved local export", "<h1>Approved editions — local export</h1><ul>" + links + "</ul>", '<a href="../index.html">Latest approved edition</a>'))
    # All pages retain LOCAL DRAFT / NOT PUBLISHED labels until publication is built.
    return len(approved)
