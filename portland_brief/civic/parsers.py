"""Source-specific structured extraction. Unknown formats stay partial."""
from datetime import datetime
import re
from urllib.parse import urljoin, urlsplit, parse_qs
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

from ..records import safe_url
from .procedures import POSITION_MAP, action_type

PARSER_VERSION = "official-html-v1"


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def field(node, label=None, name=None):
    if name:
        found = node.select_one(".field--name-" + name)
    else:
        labelnode = next((x for x in node.select(".field__label") if text(x).casefold() == label.casefold()), None)
        found = labelnode.parent if labelnode else None
    if not found:
        return ""
    values = found.select(".field__item")
    if values:
        return "; ".join(text(v) for v in values)
    result = text(found)
    labelnode = found.select_one(".field__label")
    return result.removeprefix(text(labelnode)).strip() if labelnode else result


def soup_main(data):
    soup = BeautifulSoup(data, "html.parser")
    return soup.find("main") or soup


def page_info(data, url):
    main = soup_main(data)
    nextlink = next((a for a in main.select("a[href]") if "Next page" in text(a) or a.get("rel") == ["next"]), None)
    nexturl = safe_url(urljoin(url, nextlink["href"]), [urlsplit(url).hostname]) if nextlink else None
    count = re.search(r"Displaying\s+\d[\d,]*\s*[-–]\s*\d[\d,]*\s+of\s+(\d[\d,]*)", text(main))
    return {"next_url": nexturl, "expected_count": int(count[1].replace(",", "")) if count else None}


def committees(data, url):
    main, rows = soup_main(data), []
    for heading in main.select("h4"):
        link = heading.select_one('a[href*="/council/agenda/"]')
        if not link:
            continue
        nodes = []
        for node in heading.next_siblings:
            if getattr(node, "name", None) == "h4":
                break
            if getattr(node, "name", None):
                nodes.append(node)
        roster = next((n for n in nodes if n.name == "ul"), None)
        function = next((text(n) for n in nodes if n.name == "p" and n.select_one("em")), "")
        members = []
        for li in roster.select("li") if roster else []:
            value = text(li)
            name, sep, role = value.partition(",")
            members.append({"name": name.strip(), "role": role.strip() if sep else "member"})
        rows.append({"name": text(link), "url": safe_url(urljoin(url, link["href"]), ["www.portland.gov"]), "kind": "committee_of_whole" if "of the Whole" in text(link) else "committee", "functions": function, "members": members, "locator": "h4:" + text(link), "passage": " ".join([text(heading)] + [text(n) for n in nodes])})
    if not rows or any(not r["members"] for r in rows):
        raise ValueError("Incomplete committee roster parse")
    return rows


def calendar(data, url):
    main, rows = soup_main(data), []
    for link in main.select('h2 a[href*="/events/"]'):
        card = link.find_parent("h2")
        timestamp = card.find("time", datetime=True)
        if not timestamp:
            continue
        group = timestamp.find_parent(class_="field-group")
        explicit = text(group)
        times = re.findall(r"\b(\d{1,2}:\d{2}\s*[ap]m)\b", explicit, re.I)
        day = timestamp["datetime"][:10]
        start, end = None, None
        if times:
            start = datetime.strptime(day + " " + times[0].upper().replace(" ", ""), "%Y-%m-%d %I:%M%p").replace(tzinfo=ZoneInfo("America/Los_Angeles")).isoformat()
            if len(times) > 1:
                end = datetime.strptime(day + " " + times[1].upper().replace(" ", ""), "%Y-%m-%d %I:%M%p").replace(tzinfo=ZoneInfo("America/Los_Angeles")).isoformat()
        name = text(link)
        kind = "executive_session" if "Executive" in name else "work_session" if "Work Session" in name else "committee" if "Committee" in name else "regular"
        rows.append({"title": name, "url": safe_url(urljoin(url, link["href"]), ["www.portland.gov"]), "starts_at": start, "ends_at": end, "kind": kind, "scheduled_date": day, "tentative": "tentative" in name.casefold(), "locator": "event:" + link["href"], "passage": text(card)})
    if not rows:
        raise ValueError("No actual calendar event cards parsed")
    return rows


def document_index(data, url):
    rows = []
    for card in soup_main(data).select(".views-row"):
        link = card.select_one('h2 a[href*="/council/documents/"]')
        if not link:
            continue
        rows.append({"title": text(link), "url": safe_url(urljoin(url, link["href"]), ["www.portland.gov"]), "document_number": field(card, name="field-document-number"), "document_type": text(card.select_one(".badge")).removeprefix("Label:").strip(), "introduced_by": field(card, name="field-introduced-by"), "official_disposition": field(card, label="Disposition"), "final_disposition_date": field(card, label="Date of final disposition"), "locator": "document:" + link["href"], "passage": text(card)})
    if not rows:
        raise ValueError("No document cards parsed")
    return rows


def parse_vote_text(value):
    votes, tallies = [], {}
    for match in re.finditer(r"\b(Aye|Nay|Yea|Abstain|Abstention|Absent|Excused|Disqualified)\s*\((\d+)\):\s*(.*?)(?=;\s*(?:Aye|Nay|Yea|Abstain|Abstention|Absent|Excused|Disqualified)\s*\(|\)\s*$|$)", value, re.I):
        position = POSITION_MAP.get(match[1].casefold(), "unknown")
        tallies[position] = int(match[2])
        names = match[3].rstrip(") ")
        for name in names.split(","):
            if name.strip():
                votes.append({"raw_name": name.strip(), "position": position})
    return votes, tallies


def agenda(data, url, body_kind="council"):
    main, sessions = soup_main(data), []
    for session in main.select(".relation--type-council-session"):
        timestamp = session.select_one(".session-meta h2 time[datetime]")
        if not timestamp:
            raise ValueError("Session lacks actual machine-readable time")
        attendance = field(session.select_one(".session-meta"), name="field-council-in-attendance").split("; ")
        items = []
        for item in session.select(".relation--type-agenda-item"):
            titlelink = item.select_one(".council-document__title a[href]")
            disposition = field(item, name="field-agenda-item-disposition")
            notes = field(item, name="field-disposition-notes")
            note_node = item.select_one(".field--name-field-disposition-notes .field__item")
            note_lines = [line.strip() for line in note_node.get_text("\n", strip=True).splitlines()] if note_node else []
            motions = []
            for line in note_lines:
                if not line.startswith("Motion "):
                    continue
                kind = "amendment" if line.startswith("Motion to amend ") else "procedural_motion"
                named, tallies = parse_vote_text(line)
                motions.append({"official_text": line, "kind": kind, "withdrawn": "Motion withdrawn" in line, "individual_votes": named, "declared_tallies": tallies})
            rollcall, tallies = [], {}
            vote_field = item.select_one(".field--name-field-votes")
            if vote_field:
                for group in vote_field.select("strong"):
                    label = text(group)
                    match = re.match(r"(\w+)\s*\((\d+)\)", label)
                    if not match:
                        continue
                    position = POSITION_MAP.get(match[1].casefold(), "unknown")
                    tallies[position] = int(match[2])
                    for name in group.parent.select("ul li"):
                        rollcall.append({"raw_name": text(name), "position": position})
            document_number = field(item, name="field-document-number")
            document_type = text(item.select_one(".council-document__title"))
            kind = "emergency_ordinance" if "Emergency Ordinance" in document_type else "ordinary_ordinance" if "(Ordinance)" in document_type else "resolution" if "(Resolution)" in document_type else "report_acceptance" if "(Report)" in document_type else "unknown"
            number = text(item.select_one("h4"))
            neighborhoods = field(item, name="field-neighborhood")
            registration = [safe_url(urljoin(url, a["href"]), ["www.portland.gov"]) for a in item.select('a[href*="testimony-registration"]')]
            items.append({"official_relation_id": item.get("id"), "agenda_number": number, "title": text(titlelink) if titlelink else "Procedural agenda item " + number, "document_url": safe_url(urljoin(url, titlelink["href"]), ["www.portland.gov"]) if titlelink else None, "document_number": document_number or None, "ordinance_number": field(item, label="Ordinance number") or None, "resolution_number": field(item, label="Resolution number") or None, "introduced_by": field(item, name="field-introduced-by"), "responsible_department": field(item, name="field-bureau"), "official_disposition": disposition, "action_type": action_type(disposition, body_kind), "notes": notes, "agenda_note": field(item, name="field-agenda-item-note"), "motions": motions, "individual_votes": rollcall, "declared_tallies": tallies, "event_kind": kind, "neighborhood_fields": neighborhoods, "registration_urls": registration, "locator": "#" + item.get("id", ""), "passage": text(item)})
        video = session.select_one('iframe[src*="youtube.com/embed/"]')
        sessions.append({"official_relation_id": session.get("id"), "starts_at": timestamp["datetime"], "status": field(session.select_one(".session-meta"), name="field-session-status"), "attendance": [x for x in attendance if x], "items": items, "recording_url": video.get("src").split("?")[0].replace("/embed/", "/watch?v=") if video else None, "locator": "#" + session.get("id", ""), "passage": text(session.select_one(".session-meta"))})
    if not sessions:
        raise ValueError("No official agenda sessions parsed; not a complete parse")
    return sessions


def vote_register(data, url):
    main = soup_main(data)
    result = []
    for table in main.select("table"):
        if not table.select_one(".views-field-field-document-number"):
            continue
        heading = table.find_previous(["h2", "h3"])
        label = text(heading)
        try:
            date = datetime.strptime(label, "%B %d, %Y").date().isoformat()
        except ValueError:
            date = None
        for tr in table.select("tbody tr"):
            number, link, person, position = tr.select_one(".views-field-field-document-number"), tr.select_one(".views-field-field-full-document-title a"), tr.select_one(".views-field-field-name a"), tr.select_one(".views-field-field-voted-as-follows")
            if not all([number, link, person, position]):
                continue
            result.append({"document_number": text(number).split(" ")[0], "document_url": safe_url(urljoin(url, link["href"]), ["www.portland.gov"]), "title": text(link), "person_name": text(person), "person_url": safe_url(urljoin(url, person["href"]), ["www.portland.gov"]), "position": POSITION_MAP.get(text(position).casefold(), "unknown"), "date": date, "locator": f"vote-register:{len(result)}", "passage": text(tr)})
    if not result:
        raise ValueError("No voting register rows parsed")
    return result


def county_archive(data, url):
    soup = soup_main(data)
    rows = []
    for tr in soup.select("tr"):
        link = tr.find("a", href=re.compile("AgendaViewer\\.php"))
        if not link:
            continue
        query = parse_qs(urlsplit(link["href"]).query)
        ident = ("event", query["event_id"][0]) if query.get("event_id") else ("clip", query["clip_id"][0]) if query.get("clip_id") else None
        if not ident:
            continue
        cells = tr.find_all("td", recursive=False)
        name = text(cells[0]) if cells else text(tr).split("Agenda")[0]
        kind = "executive_session" if "Executive" in name else "briefing" if "Briefing" in name else "hearing" if "Hearing" in name else "regular" if "Regular" in name else "other"
        raw = text(tr)
        epoch = re.search(r"\b(1[6789]\d{8})\b", raw)
        start = datetime.fromtimestamp(int(epoch[1]), ZoneInfo("America/Los_Angeles")).isoformat() if epoch else None
        links = {text(a).casefold(): safe_url(urljoin(url, a["href"]), ["multnomah.granicus.com", "archive-video.granicus.com"]) for a in tr.select('a[href]') if "granicus.com" in a["href"] and not a["href"].startswith("javascript")}
        rows.append({"official_identifier": ":".join(ident), "title": name, "kind": kind, "starts_at": start, "agenda_url": safe_url(urljoin(url, link["href"]), ["multnomah.granicus.com"]), "links": links, "locator": ident[0] + ":" + ident[1], "passage": raw})
    if not rows:
        raise ValueError("No official county archive rows parsed")
    return rows


def document_detail(data, url):
    main = soup_main(data)
    number = field(main, name='field-document-number')
    if not number:
        raise ValueError('Official document number not found')
    names = {'purpose': 'field-purpose-and-background', 'stated_costs': 'field-financial-impacts', 'community_impacts': 'field-community-impacts', 'economic_impacts': 'field-economic-impacts', 'introduced_by': 'field-introduced-by', 'responsible_department': 'field-bureau', 'requested_meeting_date': 'field-requested-council-date', 'requested_start_time': 'field-requested-start-time'}
    facts = {key: field(main, name=name) for key, name in names.items()}
    attachments = []
    node = main.select_one('.field--name-field-documents-and-exhibits')
    for link in node.select('a[href]') if node else []:
        target = urljoin(url, link['href'])
        if '/documents/' in target or '/sites/' in target:
            attachments.append({'url': safe_url(target, ['www.portland.gov']), 'label': text(link)})
    return {'document_number': number, 'title': text(main.select_one('h1')), 'facts': facts, 'attachments': attachments}


def event_detail(data, url):
    main = soup_main(data)
    textvalue = text(main)
    instructions = []
    for node in main.select('p,li'):
        value = text(node)
        if re.search(r'testimony|registration|register to|written comments|participat|public comment', value, re.I):
            instructions.append(value)
    links = [{'label': text(a), 'url': safe_url(urljoin(url, a['href']), ['www.portland.gov'])} for a in main.select('a[href]') if a['href'].startswith('/') and re.search(r'testimony|council-engage|/council/agenda/\d{4}/', a['href'])]
    agenda_urls = [x['url'] for x in links if re.search(r'/council/agenda/\d{4}/', x['url'])]
    return {'title': text(main.select_one('h1')), 'participation_instructions': sorted(set(instructions)), 'official_links': links, 'explicit_agenda_urls': agenda_urls, 'agenda_status': 'linked' if agenda_urls else 'no_dated_agenda_link_found', 'passage': textvalue}


def county_agenda(data, url):
    soup = soup_main(data)
    items = []
    for cell in soup.select('td.numberspace'):
        number = text(cell)
        if not re.fullmatch(r'(?:C|R)\.\d+', number):
            continue
        table = cell.find_parent('table')
        title = text(cell.find_next_sibling('td'))
        links = []
        for node in table.next_siblings:
            if getattr(node, 'name', None) == 'table':
                break
            if getattr(node, 'select', None):
                for a in node.select('a[href*="MetaViewer.php"]'):
                    links.append({'url': safe_url(urljoin(url, a['href']), ['multnomah.granicus.com']), 'label': text(a)})
        items.append({'agenda_number': number, 'title': title, 'consent_candidate': number.startswith('C.'), 'attachments': links, 'locator': 'agenda-item:' + number, 'passage': title + ' ' + '; '.join(a['label'] for a in links), 'official_disposition': '', 'action_type': 'unknown'})
    if not items:
        raise ValueError('No actionable county agenda items parsed')
    instructions = [text(cell.find_next_sibling('td')) for cell in soup.select('td.numberspace') if re.search(r'testif|testimony|public comment|deadline', text(cell.find_next_sibling('td')), re.I)]
    participation_links = [{'url': safe_url(a['href'], ['www.multco.us', 'multco.us']), 'label': text(a)} for a in soup.select('a[href]') if urlsplit(a['href']).hostname in {'www.multco.us', 'multco.us'} and 'testimony' in a['href']]
    return {'items': items, 'participation_instructions': instructions, 'participation_links': participation_links, 'parser_scope': 'agenda proposals only; no minutes or outcomes inferred'}
