"""Permanent local-government news prominence, independent of election dates."""
import re


GOVERNMENT = re.compile(r'\b(?:city council|councilors?|city hall|mayor|city auditor|county (?:chair|commission|board)|commissioners?|local government|city budget|county budget|city bureaus?|ballot measure|ranked.choice voting|municipal election)\b', re.I)


def known_name(item, settings):
    text = item.title + ' ' + item.text
    return any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I)
               for name in settings.get('government_names', []) if len(name.split()) >= 2)


def is_government(item, settings):
    text = item.title + ' ' + item.text
    return bool(GOVERNMENT.search(text) or re.search(r'\b(?:Portland|Multnomah)\b.{0,60}\b(?:elections?|candidates?)\b',text,re.I)) or known_name(item,settings)


def order_stories(stories, evidence, settings):
    """Reserve prominence after validation, without admitting unsupported news."""
    for story in stories:
        if is_government(evidence[story.citation_ids[0]],settings):
            if 'local_government' not in story.flags:
                story.flags.append('local_government')
    return sorted(stories,key=lambda s: 'local_government' not in s.flags)
