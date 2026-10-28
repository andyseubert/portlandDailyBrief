from collections import Counter
from datetime import timedelta
import re
from .records import canonical_url, parse_time, words
from .government import is_government, known_name

LOCAL = re.compile(r"\b(portland|multnomah|gresham|troutdale|fairview|wood village|PPS|providence park|willamette river|lents|hazelwood|st\.? johns|roosevelt high|sizzle pie)\b", re.I)
OUTSIDE = re.compile(r"\b(salem|keizer|deschutes|bend|eugene|la grande|tillamook|hillsboro|tigard|newberg|beaverton|oregon coast|washington['’]s|gulf coast)\b", re.I)
PROMO = re.compile(r"\b(free tickets|sponsored|executive MBA|good morning, news|spin cycle|black byline)\b", re.I)


def priority_matches(item, settings):
    text = item.title + ' ' + item.text
    return [p for p in settings.get('editorial_priorities', [])
            if any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I)
                   for term in p['terms'])]


def official_case_ids(item):
    # Only explicit case identifiers; never derive project identity from a title.
    return {re.sub(r'\s+', '', match).upper() for match in re.findall(
        r'\b(?:LU|LUR)\s*\d{2,4}\s*[-–]\s*\d{5,8}|\bT[1-4]-\d{4}-\d{4,8}',
        item.title + ' ' + item.text, re.I)}


def relevant(item, settings):
    if item.source_id in {"portland", "multnomah"} or item.source_id in settings.get('official_local_source_ids', []):
        return True
    if PROMO.search(item.title):
        return False
    if known_name(item, settings):
        return True
    # A comparison to Portland is not itself an explicit Portland impact.
    if OUTSIDE.search(item.title) and re.search(r"modeled after|similar to|inspired by", item.title, re.I):
        return False
    if LOCAL.search(item.title):
        return True
    if OUTSIDE.search(item.title):
        return False
    text = re.sub(r"^PORTLAND,?\s+Ore\.?\s*\([^)]*\)\s*[—–-]*", "", item.text, flags=re.I)
    return bool(LOCAL.search(text))


def overlapping(a, b, settings=None):
    if canonical_url(a.url) == canonical_url(b.url):
        return True
    if settings:
        def named_people(item):
            return {name for name in settings.get('government_names',[]) if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',item.title,re.I)}
        people_a, people_b = named_people(a), named_people(b)
        if people_a and people_b and people_a.isdisjoint(people_b):
            return False
    ca, cb = official_case_ids(a), official_case_ids(b)
    if ca and cb:
        if ca.isdisjoint(cb):
            return False
        # Different procedural stages of one project remain distinct news events.
        # A decision heading alone still says nothing about approval or denial.
        stage = r'\b(proposal|application|decision|approval|denial|permit|construction|opening)\b'
        sa, sb = re.search(stage, a.title, re.I), re.search(stage, b.title, re.I)
        if sa and sb and sa[1].casefold() != sb[1].casefold():
            return False
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
        score = 3 if item.source_id in {"portland", "multnomah"} or item.source_id in settings.get('official_local_source_ids', []) else 0
        score += 2 if re.search(r"council|schools?|ice|fire|government|court|budget|water|closure", t) else 0
        score += 1 if re.search(r"portland|multnomah", t) else 0
        score += sum(p['weight'] for p in priority_matches(item, settings))
        # Government coverage has permanent prominence, including after voting.
        government = bool(settings.get('government_watch', {}).get('enabled')) and is_government(item,settings)
        return government, score, parse_time(item.published_at).timestamp()
    eligible.sort(key=rank, reverse=True)
    selected, clusters, source_counts = [], {}, Counter()
    for item in eligible:
        group = settings.get('source_balance_groups', {}).get(item.source_id, item.source_id)
        duplicate = next((chosen for chosen in selected if overlapping(item, chosen, settings)), None)
        if duplicate:
            clusters.setdefault(duplicate.id, []).append(item.id)
            counts["duplicate_coverage"] += 1
        elif source_counts[group] >= settings["per_source_limit"]:
            counts["source_balance"] += 1
        elif len(selected) < settings["story_limit"]:
            selected.append(item)
            source_counts[group] += 1
        else:
            counts["story_limit"] += 1
    return selected, {"excluded": dict(counts), "overlapping_evidence_ids": clusters, "eligible_count": len(eligible),
                      "selected_priorities": {i.id: [p['id'] for p in priority_matches(i, settings)] for i in selected}}
