"""Escaped static drafts with atomic version pointers and deterministic archives."""
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


CSS = """body{margin:0;background:#f5f3ee;color:#172b30;font:18px/1.65 system-ui,sans-serif}main{max-width:780px;margin:auto;padding:36px 22px 70px}a{color:#07566a;text-underline-offset:3px}a:focus{outline:3px solid #db830c}h1{font-size:2.2rem;line-height:1.15}h2{font-size:1.35rem;line-height:1.3}.notice{background:#fff0c9;border-left:4px solid #b76b00;padding:14px 18px}article{padding:20px 0;border-bottom:1px solid #c9cfcc}.meta{font-size:.88rem;color:#44585c}li{margin-bottom:9px}footer{margin-top:32px}nav{display:flex;gap:20px;flex-wrap:wrap}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ccc;text-align:left}details{margin:16px 0}pre{white-space:pre-wrap} @media(max-width:500px){body{font-size:17px}h1{font-size:1.85rem}}"""


def local_time(value, tz):
    return datetime.fromisoformat(value).astimezone(ZoneInfo(tz)).strftime("%B %d, %Y at %-I:%M %p %Z")


def weather_lines(doc):
    w, tz = doc["weather"], doc["timezone"]
    lines = []
    for p in w.get("periods", []):
        probability = p.get("probabilityOfPrecipitation", {}).get("value")
        chance = f" Precipitation chance: {probability}%." if probability is not None else ""
        lines.append(f"{p['name']}: {p['shortForecast']}; {p['temperature']}°{p['temperatureUnit']}. Wind {p['windSpeed']} {p['windDirection']}.{chance}")
    if w.get("updated_at"):
        lines.append("Forecast issued " + local_time(w["updated_at"], tz) + ".")
    for alert in w.get("alerts", []):
        expiry = " Expires " + local_time(alert["expires"], tz) + "." if alert.get("expires") else ""
        lines.append(f"ALERT: {alert['headline'] or alert['event']}.{expiry}")
    if w.get("alerts_verified") and not w.get("alerts"):
        lines.append("No active NWS alerts returned for the central Portland point at the time checked.")
    lines.append(w["coverage"])
    lines.extend(w.get("gaps", []))
    return lines


def markdown_text(text):
    # Escape source/model punctuation so it cannot introduce Markdown links/HTML.
    result = escape(str(text), quote=False)
    for char in ("\\", "[", "]", "*", "_", "`", "#"):
        result = result.replace(char, "\\" + char)
    return result


def markdown(doc, repository=False):
    esc = markdown_text
    lines = [f"# {esc(doc['title'])} — {doc['date']}", "", "**LOCAL DRAFT — NOT PUBLISHED. Editorial review required.**", "", f"Generated {local_time(doc['generated_at'], doc['timezone'])}.", f"Coverage: Portland and directly relevant Multnomah County developments; rolling {doc['window_hours']}-hour window.", "", "AI-assisted summaries from fetched evidence. These are attributed summaries, not independent reporting.", ""]
    if doc["gaps"]:
        lines += ["## Coverage gaps", ""] + ["- " + esc(g) for g in doc["gaps"]] + [""]
    lines += ["## Portland weather and alerts", ""] + ["- " + esc(w) for w in weather_lines(doc)]
    w = doc["weather"]
    if w.get("forecast_url"):
        lines.append(f"- [NWS point forecast]({w['forecast_url']})")
    if w.get("alert_url"):
        lines.append(f"- [NWS point alerts]({w['alert_url']})")
    for story in doc["stories"]:
        lines += ["", "## " + esc(story["headline"]), "", esc(" ".join(story["sentences"])), ""]
        for eid in story["citation_ids"]:
            e = doc["evidence"][eid]
            timestamp = local_time(e["published_at"], doc["timezone"]) if e.get("published_at") else "Publication time unavailable"
            lines += [f"Source: [{esc(e['source_name'])}]({e['url']}) · {timestamp} · `{eid}`", "", esc(e["context"])]
        if "overlapping_coverage_check" in story.get("flags", []):
            lines += ["", "Review note: overlapping source descriptions contain different numbers; compare their scope and timing."]
    if not doc["stories"]:
        lines += ["", "No supported Portland story summaries were produced for this window. No quota was filled."]
    if doc.get('development_watch'):
        label='Editor-reviewed permit records.' if all(p['editorial_state']=='approved' for p in doc['development_watch']) else 'Automatically checked, source-linked drafts. Uncertain details are disclosed; human approval is not implied.'
        lines += ['', '## Development Watch', '', label+' Initial discoveries are a baseline, not proof of new activity today.', '']
        for project in doc['development_watch']:
            lines += [f"### Permit {project['folder_id']}", '', *[esc(sentence) for sentence in project['sentences']], '', f"[Official permit]({project['source_url']}) · Evidence version `{project['version_id']}`", '', 'Construction, completion and business opening remain unverified.', '']
            for note in project.get('notes',[]):
                lines += [esc(note['text']), '', f"[Supporting official report]({note['source_url']})", '']
    lines += ["", "## Run information", "", f"Local model: {esc(doc['usage']['model'])}. Input tokens: {doc['usage']['input_tokens']}; output tokens: {doc['usage']['output_tokens']}; model attempts: {doc['usage']['attempts']}. API cost: $0.", "", "Publishing disabled. Scheduling " + ('enabled' if doc.get('scheduling') else 'disabled') + ". Source health and evidence are retained in the private run record.", ""]
    result = "\n".join(lines)
    if repository:
        result = result.replace("LOCAL DRAFT — NOT PUBLISHED. Editorial review required.", "REVIEW DRAFT — shared in the repository. Editorial review required.")
        result = result.replace("Publishing disabled. Scheduling " + ('enabled' if doc.get('scheduling') else 'disabled') + ". Source health and evidence are retained in the private run record.", "Daily collection and repository archiving are enabled. Source evidence and model inputs remain private. This is an AI-assisted review draft, not an editor-approved report.")
    return result


def html(doc, root_index=False, repository=False):
    e = escape
    content = [f"<h1>{e(doc['title'])}<br>{e(doc['date'])}</h1>", '<p class="notice"><strong>LOCAL DRAFT — NOT PUBLISHED.</strong> Editorial review required.</p>', f"<p class=\"meta\">Generated {e(local_time(doc['generated_at'], doc['timezone']))}. Rolling {doc['window_hours']}-hour window. Portland and directly relevant Multnomah County.</p>", "<p>AI-assisted summaries from fetched evidence. Attributed summaries, not independent reporting.</p>"]
    if doc["gaps"]:
        content += ['<section aria-labelledby="gaps"><h2 id="gaps">Coverage gaps</h2><ul>']
        content += ["<li>" + e(g) + "</li>" for g in doc["gaps"]]
        content += ["</ul></section>"]
    content += ["<section><h2>Portland weather and alerts</h2><ul>"]
    content += ["<li>" + e(line) + "</li>" for line in weather_lines(doc)]
    content += ["</ul>"]
    for name, key in [("NWS forecast", "forecast_url"), ("NWS alerts", "alert_url")]:
        if doc["weather"].get(key):
            content.append(f'<p><a href="{e(doc["weather"][key], quote=True)}" rel="noopener noreferrer">{name}</a></p>')
    content.append("</section>")
    for i, story in enumerate(doc["stories"]):
        content += [f'<article aria-labelledby="story-{i}"><h2 id="story-{i}">{e(story["headline"])}</h2>', "<p>" + e(" ".join(story["sentences"])) + "</p>"]
        for eid in story["citation_ids"]:
            record = doc["evidence"][eid]
            timestamp = local_time(record["published_at"], doc["timezone"]) if record.get("published_at") else "Publication time unavailable"
            content.append(f'<p class="meta">Source: <a href="{e(record["url"], quote=True)}" rel="noopener noreferrer">{e(record["source_name"])}</a> · {e(timestamp)}</p><p class="meta">{e(record["context"])}</p>')
        if "overlapping_coverage_check" in story.get("flags", []):
            content.append('<p class="meta">Review note: overlapping coverage has different numbers; compare scope and timing.</p>')
        content.append("</article>")
    if not doc["stories"]:
        content.append("<p>No supported stories were produced for this window; no quota was filled.</p>")
    if doc.get('development_watch'):
        label='Editor-reviewed permit records.' if all(p['editorial_state']=='approved' for p in doc['development_watch']) else 'Automatically checked, source-linked drafts. Uncertain details are disclosed; human approval is not implied.'
        content.append('<h2>Development Watch</h2><p>'+label+' Initial discoveries are a baseline, not proof of new activity today.</p>')
        for project in doc['development_watch']:
            content.append('<article><h3>Permit '+str(project['folder_id'])+'</h3>'+''.join('<p>'+e(sentence)+'</p>' for sentence in project['sentences'])+'<p><a href="'+e(project['source_url'], quote=True)+'">Official permit</a> · Evidence version '+e(project['version_id'])+'</p><p>Construction, completion and business opening remain unverified.</p></article>')
            for note in project.get('notes',[]):
                content.append('<p>'+e(note['text'])+' <a href="'+e(note['source_url'],quote=True)+'">Supporting official report</a></p>')
    operation = 'Publishing disabled; local scheduling enabled.' if doc.get('scheduling') else 'Publishing and scheduling disabled.'
    content.append(f'<footer class="meta">Local model: {e(doc["usage"]["model"])}. Input/output tokens: {doc["usage"]["input_tokens"]}/{doc["usage"]["output_tokens"]}. API cost $0. {operation}</footer>')
    nav = '<a href="archive/index.html">Dated drafts</a>' if root_index else '<a href="../../index.html">Latest draft</a><a href="../index.html">Dated drafts</a>'
    result = page(doc["title"] + " — " + doc["date"], "".join(content), nav)
    if repository:
        result = result.replace("LOCAL DRAFT — NOT PUBLISHED.", "REVIEW DRAFT — shared in the repository.")
        result = result.replace(operation, "Daily collection and repository archiving enabled; source evidence and model inputs remain private. Editorial review required.")
        old_nav = '<nav aria-label="Brief navigation">' + nav + '</nav>'
        new_nav = '<nav aria-label="Brief navigation"><a href="' + ('' if root_index else '../') + 'briefings.html">Daily archive</a><a href="' + ('posts/' if root_index else '') + doc['date'] + '.md">Read Markdown</a></nav>'
        result = result.replace(old_nav, new_nav)
    return result


def page(title, content, nav=""):
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{escape(title)}</title><style>{CSS}</style></head><body><main><nav aria-label="Brief navigation">{nav}</nav>{content}</main></body></html>'


def atomic_text(path: Path, text: str):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def write_draft(root: Path, settings: dict, doc: dict, run_id: str):
    draft = root / settings["output_path"] / "drafts"
    version = draft / "versions" / doc["date"] / run_id
    version.mkdir(parents=True)
    (version / "brief.md").write_text(markdown(doc))
    (version / "brief.html").write_text(html(doc))
    (version / "brief.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    archive = draft / "archive"
    archive.mkdir(exist_ok=True)
    date_path = archive / doc["date"]
    temp_link = archive / (doc["date"] + ".tmp")
    temp_link.symlink_to(Path("../versions") / doc["date"] / run_id, target_is_directory=True)
    os.replace(temp_link, date_path)
    dates = sorted([p.name for p in archive.iterdir() if p.is_symlink() and len(p.name) == 10], reverse=True)
    latest_doc = json.loads((archive / dates[0] / "brief.json").read_text())
    atomic_text(draft / "index.html", html(latest_doc, root_index=True))
    atomic_text(draft / "latest.md", markdown(latest_doc))
    links = "".join(f'<li><a href="{date}/brief.html">{date}</a> — local draft</li>' for date in dates)
    atomic_text(archive / "index.html", page("Portland draft archive", "<h1>Dated draft archive</h1><p>Private local drafts; none are published.</p><ul>" + links + "</ul>", '<a href="../index.html">Latest draft</a>'))
    return date_path
