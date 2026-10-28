"""RSS from validated summary-only editions, never source/evidence files."""
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .records import safe_url
from .rendering import html


def summary_html(doc):
    main = BeautifulSoup(html(doc,repository=True),'html.parser').find('main')
    for nav in main.select('nav'):
        nav.decompose()
    return str(main)


def render(documents, config):
    feed_url = safe_url(config['feed_url'])
    archive_url = safe_url(config['archive_url']).rstrip('/')+'/'
    root = ET.Element('rss',{'version':'2.0'})
    channel = ET.SubElement(root,'channel')
    for tag,value in [('title','Portland Daily Brief'),('link',safe_url(config['channel_url'])),('description','Portland news, permanent council and local-government coverage, development and election updates. AI-assisted review drafts; meeting narratives require editorial approval.'),('language','en-us')]:
        ET.SubElement(channel,tag).text=value
    ET.SubElement(channel,'{http://www.w3.org/2005/Atom}link',{'href':feed_url,'rel':'self','type':'application/rss+xml'})
    editions = sorted(documents,key=lambda d:d['date'],reverse=True)[:config.get('maximum_editions',30)]
    if editions:
        latest=max(datetime.fromisoformat(d['generated_at']).astimezone(timezone.utc) for d in editions)
        ET.SubElement(channel,'lastBuildDate').text=format_datetime(latest,usegmt=True)
    for doc in editions:
        item=ET.SubElement(channel,'item')
        ET.SubElement(item,'title').text=doc['title']+' — '+doc['date']
        link=archive_url+'posts/'+doc['date']+'.md'
        ET.SubElement(item,'link').text=link
        ET.SubElement(item,'guid',{'isPermaLink':'false'}).text=feed_url+'#'+doc['date']
        published=datetime.fromisoformat(doc['generated_at']).astimezone(timezone.utc)
        ET.SubElement(item,'pubDate').text=format_datetime(published,usegmt=True)
        ET.SubElement(item,'description').text=summary_html(doc)
    ET.register_namespace('atom','http://www.w3.org/2005/Atom')
    return ET.tostring(root,encoding='unicode',xml_declaration=True)+'\n'
