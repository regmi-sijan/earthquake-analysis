"""Readers, cached transport, and conservative event matching for Mw enrichment."""
import csv
import gzip
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .magnitudes import MW_TYPES, finite_number

USGS = 'https://earthquake.usgs.gov/fdsnws/event/1/query'
ISC = 'https://www.isc.ac.uk/fdsnws/event/1/query'
CMT = 'https://www.ldeo.columbia.edu/~gcmt/projects/CMT/catalog/jan76_dec25.ndk.gz'


def timestamp(value):
    parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def distance(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 12742.0176*math.asin(math.sqrt(min(1, max(0, h))))


class Cache:
    def __init__(self, root, offline=False, timeout=45):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.offline, self.timeout = offline, timeout

    def invalidate(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        for suffix in ['.data', '.json']:
            (self.root/(key+suffix)).unlink(missing_ok=True)

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root/(key+'.data')
        meta = self.root/(key+'.json')
        if path.exists() and meta.exists():
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != json.loads(meta.read_text())['sha256']:
                raise ValueError('Cache checksum mismatch: '+str(path))
            return data
        if self.offline:
            raise RuntimeError('Not cached: '+url)
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={'User-Agent':'EarthquakeResearch/1.0'})
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = response.read()
                break
            except urllib.error.HTTPError as error:
                if error.code == 204:
                    data = b''; break
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                time.sleep(2**attempt)
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2: raise
                time.sleep(2**attempt)
        temp = path.with_suffix('.tmp'); temp.write_bytes(data); temp.replace(path)
        meta.write_text(json.dumps({'url':url, 'retrieved_utc':iso(time.time()),
                                   'sha256':hashlib.sha256(data).hexdigest()}, indent=2))
        time.sleep(.2)
        return data


def quakeml(data, source, url):
    if not data.strip(): return []
    root = ET.fromstring(data)
    # Strip namespaces, retaining local attribute names for event aliases.
    for node in root.iter():
        node.tag = node.tag.split('}')[-1]
    if root.tag != 'quakeml' and root.find('.//eventParameters') is None:
        raise ValueError('Response is not QuakeML')
    events = []
    for e in root.findall('.//event'):
        origins = {o.get('publicID'):o for o in e.findall('origin')}
        preferred = e.findtext('preferredOriginID')
        origin = origins.get(preferred)
        if origin is None:
            origin = next(iter(origins.values()), None)
        if origin is None: raise ValueError('Event without origin')
        aliases = []
        attrs = {k.split('}')[-1]:v for k,v in e.attrib.items()}
        if attrs.get('eventsource') and attrs.get('eventid'):
            aliases.append(attrs['eventsource']+attrs['eventid'])
        event_id = e.get('publicID', '')
        # USGS QuakeML publicIDs contain eventid=... or /event/<id>.
        q = urllib.parse.parse_qs(urllib.parse.urlparse(event_id).query)
        aliases.extend(q.get('eventid', []))
        mags = []
        for m in e.findall('magnitude'):
            kind = (m.findtext('type') or '').strip().lower()
            value = finite_number(m.findtext('mag/value'))
            if kind in MW_TYPES and value is not None:
                mags.append({'value':value, 'type':kind,
                             'agency':m.findtext('creationInfo/agencyID') or '',
                             'uncertainty':finite_number(m.findtext('mag/uncertainty')),
                             'magnitude_id':m.get('publicID',''),
                             'preferred':m.get('publicID') == e.findtext('preferredMagnitudeID'),
                             'basis':'reported_mw'})
        lat = finite_number(origin.findtext('latitude/value'))
        lon = finite_number(origin.findtext('longitude/value'))
        depth = finite_number(origin.findtext('depth/value'))
        if lat is None or lon is None: raise ValueError('Event missing coordinates')
        events.append({'source':source, 'event_id':event_id, 'aliases':aliases,
                       'time':timestamp(origin.findtext('time/value')),
                       'latitude':lat, 'longitude':lon,
                       'depth':depth/1000 if depth is not None else None,
                       'magnitudes':mags, 'url':url})
    return events


def ndk(data, url=CMT):
    if data[:2] == b'\x1f\x8b': data = gzip.decompress(data)
    lines = [line for line in data.decode().splitlines() if line.strip()]
    if len(lines)%5: raise ValueError('Truncated NDK file')
    result=[]
    for i in range(0,len(lines),5):
        a,b,c,d,e = lines[i:i+5]
        # Reference hypocenter, NOT centroid, used for cross-catalog matching.
        date = datetime.strptime(a[5:15], '%Y/%m/%d').replace(tzinfo=timezone.utc)
        hh,mm,ss = a[16:26].split(':')
        t = (date+timedelta(hours=int(hh),minutes=int(mm),seconds=float(ss))).timestamp()
        moment = float(e[49:56]); exponent = int(d[:2])
        mw = (2/3)*(math.log10(moment)+exponent-16.1)
        result.append({'source':'global_cmt','event_id':b[:16].strip(),'aliases':[],
                       'time':t,'latitude':float(a[27:33]),'longitude':float(a[34:41]),
                       'depth':float(a[42:47]),'url':url,
                       'magnitudes':[{'value':round(mw,4),'type':'mw','agency':'GCMT',
                                      'uncertainty':None,'magnitude_id':b[:16].strip(),
                                      'preferred':True,'basis':'scalar_moment'}]})
    return result


def normalized_iscgem(path):
    """Explicit normalized import; never infer direct Mw from an unknown flag."""
    result=[]
    with Path(path).open(newline='') as f:
        reader=csv.DictReader(f)
        required={'event_id','time_utc','latitude','longitude','depth_km','mw','mw_basis','reference'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError('ISC-GEM normalized CSV requires: '+', '.join(sorted(required)))
        for row in reader:
            if row['mw_basis'] not in {'direct','converted','unknown'}:
                raise ValueError('mw_basis must explicitly be direct, converted, or unknown')
            value=finite_number(row['mw'])
            if value is None: continue
            result.append({'source':'isc_gem','event_id':row['event_id'],'aliases':[],
                           'time':timestamp(row['time_utc']),'latitude':float(row['latitude']),
                           'longitude':float(row['longitude']),'depth':finite_number(row['depth_km']),
                           'url':row['reference'], 'magnitudes':[{'value':value,'type':'mw',
                           'agency':'ISC-GEM','uncertainty':None,'magnitude_id':row['event_id'],
                           'preferred':True,'basis':row['mw_basis']}]})
    return result


def fetch_window(cache, source, start, end, bounds=None):
    base = USGS if source == 'usgs' else ISC
    params={'format':'xml','starttime':iso(start),'endtime':iso(end),
            'includeallmagnitudes':'true','includeallorigins':'true',
            'limit':20000,'orderby':'time-asc','nodata':204}
    if source == 'isc':
        # ISC rejects a trailing Z even though its times are interpreted as UTC.
        params['starttime'] = iso(start).removesuffix('Z')
        params['endtime'] = iso(end).removesuffix('Z')
    if bounds:
        params.update(dict(zip(['minlatitude','maxlatitude','minlongitude','maxlongitude'],bounds)))
    url=base+'?'+urllib.parse.urlencode(params)
    try:
        data=cache.get(url)
        if data.lstrip().startswith(b'Error '):
            cache.invalidate(url)
            raise RuntimeError(data.decode(errors='replace')[:500])
        try:
            result=quakeml(data,source,url)
        except (ET.ParseError, ValueError):
            cache.invalidate(url)
            raise
    except urllib.error.HTTPError as error:
        # Split only an explicit result-limit error, not arbitrary invalid requests.
        detail=error.read().decode(errors='replace')
        if error.code != 400 or not any(s in detail.lower() for s in ['20000','20,000','maximum number','too many']):
            raise RuntimeError(f'{source} HTTP {error.code}: {detail[:300]}') from error
        result=None
    if result is None or len(result)>=20000:
        if end-start<=1: raise RuntimeError('Cannot subdivide saturated request')
        mid=(start+end)/2
        return fetch_window(cache,source,start,mid,bounds)+fetch_window(cache,source,mid,end,bounds)
    return result


def match_metrics(row, event, seconds=60, km=100, depth_km=100):
    dt=abs(timestamp(row['time_utc'])-event['time'])
    dist=distance(float(row['latitude']),float(row['longitude']),event['latitude'],event['longitude'])
    depth=finite_number(row.get('depth_km'))
    dz=abs(depth-event['depth']) if depth is not None and event['depth'] is not None else None
    exact=row['id'] in event['aliases'] and event['source']=='usgs'
    if exact: return {'match':'event_id','dt_seconds':dt,'distance_km':dist,'depth_difference_km':dz}
    if dt<=seconds and dist<=km and (dz is None or dz<=depth_km):
        return {'match':'time_location','dt_seconds':dt,'distance_km':dist,'depth_difference_km':dz}
    return None


def choose(candidates):
    """Never choose between nearby event IDs; select magnitude only within one event."""
    rank={'usgs':0,'global_cmt':1,'isc':2,'isc_gem':3}
    usable=[c for c in candidates if c['magnitude']['basis'] not in {'converted','unknown'}]
    for source in rank:
        group=[c for c in usable if c['source']==source]
        if not group: continue
        exact=[c for c in group if c['match']=='event_id']
        if exact: group=exact
        if len({c['event_id'] for c in group})!=1:
            return None,'ambiguous_event_match'
        values=[c['magnitude']['value'] for c in group]
        if max(values)-min(values)>.3:
            return None,'conflicting_mw_estimates'
        order={'mww':0,'mwc':1,'mwb':2,'mwr':3,'mw':4}
        group.sort(key=lambda c:(not c['magnitude']['preferred'],order.get(c['magnitude']['type'],9),
                                 c['magnitude']['agency'],c['magnitude']['magnitude_id']))
        return group[0],'matched_reported_mw'
    return None,'no_direct_mw_found'
