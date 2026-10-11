"""Bounded official permit lookups; records are evidence, not development outcomes."""
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlencode

from ..records import safe_url
from .store import stable_id

LAYER = 'https://www.portlandmaps.com/arcgis/rest/services/Public/BDS_Permit/MapServer/22'
PARSER_VERSION = 'portland-permit-lookup/1'
FIELDS = ('FOLDERKEY,PROPERTYKEY,APPLICATION,PERMIT,STATUS,HOUSE,DIRECTION,PROPSTREET,'
          'STREETTYPE,CITY,COUNTY,TYPE,WORK_DESCRIPTION,DESCRIPTION,ISSUED,FINALED,'
          'NEIGHBORHOOD,GIS_LOCATION_TYPE,GIS_PROCESS_DATE,PORTLAND_MAPS_URL')
SUFFIXES = {'ST', 'STREET', 'AVE', 'AVENUE', 'RD', 'ROAD', 'BLVD', 'BOULEVARD',
            'DR', 'DRIVE', 'CT', 'COURT', 'PL', 'PLACE', 'WAY', 'TER', 'TERRACE',
            'LN', 'LANE', 'HWY', 'HIGHWAY'}


def address_parts(address):
    if '\n' in address or '\r' in address:
        raise ValueError('Use one street address on a single line')
    normalized = ' '.join(address.upper().split())
    match = re.fullmatch(r"([0-9]{1,6}) (NE|NW|SE|SW|N|S) ([A-Z0-9][A-Z0-9 '\-]{0,70})", normalized)
    if not match:
        raise ValueError('Use a street number, direction and street: 6866 SE 87TH AVE. Use PortlandMaps for intersections or units.')
    house, direction, street = match.groups()
    words = street.split()
    if len(words) > 1 and words[-1] in SUFFIXES:
        words.pop()
    return house, direction, ' '.join(words)


def lookup(store, fetcher, address, limit=100):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Permit lookup limit must be between 1 and 200')
    house, direction, street = address_parts(address)
    # HOUSE contains leading spaces in the official service. Filter candidates
    # server-side, then compare stripped house numbers exactly below.
    where = (f"HOUSE LIKE '%{house}%' AND DIRECTION = '{direction}' "
             f"AND PROPSTREET = '{street.replace(chr(39), chr(39)*2)}'")
    url = LAYER + '/query?' + urlencode({'f': 'json', 'where': where,
          'outFields': FIELDS, 'outSR': 4326, 'resultRecordCount': limit,
          'orderByFields': 'OBJECTID DESC'})
    try:
        raw, meta = fetcher.get(url)
        if meta.get('status') == 'stale_cache':
            raise RuntimeError('Current permit lookup failed; stale results cannot establish current status')
        response = json.loads(raw)
        if not isinstance(response, dict) or response.get('error') or not isinstance(response.get('features'), list):
            raise ValueError('Official permit service returned an error or invalid feature collection')
        if response.get('spatialReference', {}).get('wkid') != 4326:
            raise ValueError('Permit geometry has an unverified coordinate reference system')
        candidates = []
        ids = set()
        for feature in response['features']:
            attrs = feature.get('attributes') if isinstance(feature, dict) else None
            if not isinstance(attrs, dict) or not set(FIELDS.split(',')).issubset(attrs):
                raise ValueError('Official permit record is missing expected fields; parsing is incomplete')
            if (str(attrs['HOUSE']).strip(), attrs['DIRECTION'], attrs['PROPSTREET']) != (house, direction, street):
                continue
            key = attrs['FOLDERKEY']
            if type(key) is not int or key <= 0 or key in ids:
                raise ValueError('Missing or duplicate official permit identity requires review')
            ids.add(key)
            if not isinstance(attrs['PORTLAND_MAPS_URL'], str):
                raise ValueError('Official permit detail link is missing')
            link = safe_url(attrs['PORTLAND_MAPS_URL'], ['www.portlandmaps.com'])
            if link != f'https://www.portlandmaps.com/detail/permit/{key}_did/':
                raise ValueError('Official permit identifier and detail link disagree')
            candidates.append({'id': stable_id('portland-permit', key),
                'official_folder_id': key, 'official_application_number': attrs['APPLICATION'],
                'permit_category': attrs['PERMIT'], 'official_status': attrs['STATUS'],
                'source_url': link, 'official_fields': attrs, 'source_geometry': feature.get('geometry'),
                'neighborhood_method': 'official_permit_field; not independently spatially verified',
                'implementation_status': 'unknown'})
        partial = bool(response.get('exceededTransferLimit'))
        meaningful = {'records': sorted(candidates, key=lambda r: r['official_folder_id']), 'partial': partial}
        version, changed = store.source_version(url, raw, PARSER_VERSION, meaningful,
                                               pipeline='filings', retrieved_at=meta['fetched_at'])
        for record in candidates:
            passage = store.passage(version, f"FOLDERKEY={record['official_folder_id']}",
                                    json.dumps(record['official_fields'], sort_keys=True))
            record.update(source_version=version, passage_id=passage)
        store.commit()
        return {'address_query': address, 'source_url': url, 'source_version': version,
                'retrieved_at': meta['fetched_at'],
                'checked_at': datetime.fromtimestamp(meta.get('checked_epoch', datetime.now(timezone.utc).timestamp()), timezone.utc).isoformat(),
                'material_change': changed, 'records': candidates, 'status': 'partial' if partial else 'processed',
                'coverage': 'bounded address query of the official permit layer; not a complete development inventory',
                'note': 'No matches does not establish that no project exists. Permits remain separate records; an issued permit does not establish construction or opening.',
                'published': False, 'model_tokens': 0}
    except Exception as exc:
        store.failed_retrieval('filings', url, exc)
        raise
