from collections import Counter
from datetime import timedelta
import re
from .records import canonical_url, parse_time, words

LOCAL = re.compile(r"\b(portland|multnomah|gresham|troutdale|fairview|wood village|PPS|providence park|willamette river|lents|hazelwood|st\.? johns|roosevelt high|sizzle pie)\b", re.I)
OUTSIDE = re.compile(r"\b(salem|keizer|deschutes|bend|eugene|la grande|tillamook|hillsboro|tigard|newberg|beaverton|oregon coast|washington['’]s|gulf coast)\b", re.I)
PROMO = re.compile(r"\b(free tickets|sponsored|executive MBA|good morning, news|spin cycle|black byline)\b", re.I)


def relevant(item, settings):
    if item.source_id in {"portland", "multnomah"}:
        return True
    if PROMO.search(item.title):
        return False
    # A comparison to Portland is not itself an explicit Portland impact.
    if OUTSIDE.search(item.title) and re.search(r"modeled after|similar to|inspired by", item.title, re.I):
        return False
    if LOCAL.search(item.title):
        return True
    if OUTSIDE.search(item.title):
        return False
    text = re.sub(r"^PORTLAND,?\s+Ore\.?\s*\([^)]*\)\s*[—–-]*", "", item.text, flags=re.I)
    return bool(LOCAL.search(text))


def overlapping(a, b):
    if canonical_url(a.url) == canonical_url(b.url):
        return True
    wa, wb = words(a.title), words(b.title)
    if wa and wb and len(wa & wb) / min(len(wa), len(wb)) >= .65:
        return True
    # Common independently phrased local event coverage.
    ta, tb = (a.title + " " + a.text).lower(), (b.title + " " + b.text).lower()
    themes = [("i-205", "crash"), ("hazelwood", "fire"), ("two fires",), ("2 fires",), ("retro gaming",), ("ice", "arrest"), ("dan ryan", "phone"), ("pps", "closure"), ("school closure", "portland")]
    return any(all(term in ta and term in tb for term in theme) for theme in themes)


def select(items, now, settings):
    counts, eligible = Counter(), []
    cutoff = now - timedelta(hours=settings["window_hours"])
    for item in items:
        dt = parse_time(item.published_at)
        if dt is None:
            counts["undated"] += 1
        elif dt > now:
            counts["future"] += 1
        elif dt < cutoff and item.id not in settings.get("late_arrival_ids", []):
            counts["older_than_window"] += 1
        elif not relevant(item, settings):
            counts["outside_scope_or_promotional"] += 1
        elif len(item.text.split()) < 12:
            counts["insufficient_evidence"] += 1
        else:
            eligible.append(item)
    def rank(item):
        t = item.title.lower()
        score = 3 if item.source_id in {"portland", "multnomah"} else 0
        score += 2 if re.search(r"council|schools?|ice|fire|government|court|budget|water|closure", t) else 0
        score += 1 if re.search(r"portland|multnomah", t) else 0
        return score, parse_time(item.published_at).timestamp()
    eligible.sort(key=rank, reverse=True)
    selected, clusters, source_counts = [], {}, Counter()
    for item in eligible:
        duplicate = next((chosen for chosen in selected if overlapping(item, chosen)), None)
        if duplicate:
            clusters.setdefault(duplicate.id, []).append(item.id)
            counts["duplicate_coverage"] += 1
        elif source_counts[item.source_id] >= settings["per_source_limit"]:
            counts["source_balance"] += 1
        elif len(selected) < settings["story_limit"]:
            selected.append(item)
            source_counts[item.source_id] += 1
        else:
            counts["story_limit"] += 1
    return selected, {"excluded": dict(counts), "overlapping_evidence_ids": clusters, "eligible_count": len(eligible)}
