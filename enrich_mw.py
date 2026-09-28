#!/usr/bin/env python3
"""Enrich local USGS catalogs with reported Mw from documented public sources."""
import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from earthquake_analysis.enrichment import (Cache,CMT,fetch_window,ndk,normalized_iscgem,
                                           timestamp,iso,match_metrics,choose)
from earthquake_analysis.magnitudes import MW_TYPES,finite_number

EXTRA=['reported_mw','reported_mw_type','reported_mw_source','reported_mw_agency',
       'reported_mw_uncertainty','mw_lookup_status','mw_lookup_sources','mw_lookup_errors',
       'matched_event_id','match_dt_seconds','match_distance_km']


def build(args):
    out=args.output.resolve(); out.mkdir(parents=True,exist_ok=True)
    # Pin configuration and input hashes; safe resume only for identical input/settings.
    files=sorted(args.input.rglob('earthquakes.csv')) if args.input.is_dir() else [args.input]
    if not files or any(not p.is_file() for p in files):raise ValueError('No input CSV files found')
    manifest=[]
    for path in files:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        manifest.append({'path':str(path.resolve()),'sha256':h.hexdigest()})
    config={'inputs':manifest,'seconds':args.seconds,'km':args.km,'depth_km':args.depth_km,
            'bounds':args.bounds,'start':args.start,'end':args.end,
            'isc_gem':str(args.isc_gem.resolve()) if args.isc_gem else None,
            'cmt_url':CMT,'sources':['usgs','global_cmt','isc','isc_gem'], 'version':1}
    if args.isc_gem:config['isc_gem_sha256']=hashlib.sha256(args.isc_gem.read_bytes()).hexdigest()
    if args.cmt_file:config['cmt_sha256']=hashlib.sha256(args.cmt_file.read_bytes()).hexdigest()
    cfg=out/'settings.json'
    if cfg.exists() and json.loads(cfg.read_text())!=config:raise ValueError('Output settings differ; use a new output directory')
    cfg.write_text(json.dumps(config,indent=2)+'\n')
    (out/'RUNNING').write_text('Do not interpret outputs as complete while this file exists.\n')
    db=sqlite3.connect(out/'index.sqlite')
    db.executescript('''CREATE TABLE IF NOT EXISTS local(id TEXT PRIMARY KEY,t REAL,row TEXT);
    CREATE INDEX IF NOT EXISTS local_time ON local(t);
    CREATE TABLE IF NOT EXISTS remote(source TEXT,id TEXT,t REAL,event TEXT,PRIMARY KEY(source,id));
    CREATE INDEX IF NOT EXISTS remote_time ON remote(t);
    CREATE TABLE IF NOT EXISTS aliases(alias TEXT,source TEXT,id TEXT,PRIMARY KEY(alias,source,id));
    CREATE TABLE IF NOT EXISTS batch(source TEXT,start REAL,end REAL,status TEXT,error TEXT,
                                    PRIMARY KEY(source,start,end));
    CREATE TABLE IF NOT EXISTS link(local_id TEXT,source TEXT,remote_id TEXT,candidate TEXT);
    CREATE INDEX IF NOT EXISTS link_source ON link(source,remote_id);
    CREATE INDEX IF NOT EXISTS link_local ON link(local_id);
    ''')
    db.execute('DELETE FROM local');db.execute('DELETE FROM link')
    columns=None;excluded=0
    for path in files:
        with path.open(newline='',encoding='utf-8-sig') as f:
            reader=csv.DictReader(f)
            if not {'id','time_utc','latitude','longitude','magnitude','magnitude_type'}<=set(reader.fieldnames or []):
                raise ValueError('Invalid input schema: '+str(path))
            if columns is None:columns=reader.fieldnames
            if reader.fieldnames!=columns or set(columns)&set(EXTRA):raise ValueError('Inconsistent/already-enriched schema')
            for row in reader:
                if None in row or any(v is None for v in row.values()):raise ValueError('Malformed CSV row')
                t=timestamp(row['time_utc'])
                if (args.start and t<timestamp(args.start)) or (args.end and t>=timestamp(args.end)):
                    excluded+=1;continue
                if not row['id'].strip():raise ValueError('Missing event ID')
                lat,lon=finite_number(row['latitude']),finite_number(row['longitude'])
                if lat is None or lon is None or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid coordinates')
                try:db.execute('INSERT INTO local VALUES(?,?,?)',(row['id'],t,json.dumps(row)))
                except sqlite3.IntegrityError:raise ValueError('Duplicate event ID: '+row['id'])
    db.commit()
    n,lo,hi=db.execute('SELECT count(*),min(t),max(t) FROM local').fetchone()
    if not n:raise ValueError('No events in selected scope')
    if args.bounds:
        # Query box must contain all input locations plus a conservative matching halo.
        south,north,west,east=args.bounds
        for (raw,) in db.execute('SELECT row FROM local'):
            r=json.loads(raw);lat=float(r['latitude']);lon=float(r['longitude'])
            halo=args.km/111.0
            lonhalo=halo/max(.01,math.cos(math.radians(lat)))
            if not south<=lat-halo or not lat+halo<=north or not west<=lon-lonhalo or not lon+lonhalo<=east:
                raise ValueError('Bounds must contain every input point plus matching-distance halo')
    cache=Cache(args.cache,args.offline,args.timeout)
    source_state={}
    def insert(events):
        for e in events:
            if lo-args.seconds<=e['time']<=hi+args.seconds:
                db.execute('INSERT OR REPLACE INTO remote VALUES(?,?,?,?)',
                           (e['source'],e['event_id'],e['time'],json.dumps(e)))
                for alias in e['aliases']:
                    db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?,?)',(alias,e['source'],e['event_id']))
        db.commit()
    try:
        data=args.cmt_file.read_bytes() if args.cmt_file else cache.get(CMT)
        insert(ndk(data));source_state['global_cmt']='searched_1976_2025'
    except Exception as e:source_state['global_cmt']='error: '+str(e)
    if args.isc_gem:
        insert(normalized_iscgem(args.isc_gem));source_state['isc_gem']='searched_local_normalized_catalog'
    else:source_state['isc_gem']='not_searched_registration_download_required'
    # Year batches for a regional box; month batches for worldwide retrieval.
    windows=[]
    first=datetime.fromtimestamp(lo,timezone.utc)
    cursor=datetime(first.year,1 if args.bounds else first.month,1,tzinfo=timezone.utc)
    while cursor.timestamp()<=hi:
        nxt=datetime(cursor.year+1,1,1,tzinfo=timezone.utc) if args.bounds or cursor.month==12 else datetime(cursor.year,cursor.month+1,1,tzinfo=timezone.utc)
        if db.execute('SELECT 1 FROM local WHERE t>=? AND t<? LIMIT 1',(cursor.timestamp(),nxt.timestamp())).fetchone():
            windows.append((cursor.timestamp()-args.seconds,nxt.timestamp()+args.seconds))
        cursor=nxt
    for source in ['usgs','isc']:
        for i,(start,end) in enumerate(windows,1):
            prior=db.execute('SELECT status FROM batch WHERE source=? AND start=? AND end=?',(source,start,end)).fetchone()
            if prior and prior[0]=='ok':continue
            print(f'{source}: batch {i}/{len(windows)} {iso(start)}',flush=True)
            try:
                events=fetch_window(cache,source,start,end,args.bounds)
                insert(events);status,error='ok',''
            except Exception as e:status,error='error',str(e)
            db.execute('INSERT OR REPLACE INTO batch VALUES(?,?,?,?,?)',(source,start,end,status,error));db.commit()
            if error:print(f'  Request failed: {error[:180]}',flush=True)
    # Match all catalog events, including those already carrying Mw, to detect reuse.
    for event_id,t,raw in db.execute('SELECT id,t,row FROM local ORDER BY t'):
        row=json.loads(raw)
        exact_usgs = {x[0] for x in db.execute(
            "SELECT id FROM aliases WHERE alias=? AND source='usgs'", (event_id,))}
        for (encoded,) in db.execute('SELECT event FROM remote WHERE t BETWEEN ? AND ? UNION SELECT r.event FROM remote r JOIN aliases a ON r.source=a.source AND r.id=a.id WHERE a.alias=?',
                                    (t-args.seconds,t+args.seconds,event_id)):
            e=json.loads(encoded)
            if e['source']=='usgs' and exact_usgs and e['event_id'] not in exact_usgs:
                continue
            metrics=match_metrics(row,e,args.seconds,args.km,args.depth_km)
            if metrics is None:continue
            for magnitude in e['magnitudes']:
                candidate={'local_id':event_id,'source':e['source'],'event_id':e['event_id'],
                           'magnitude':magnitude,'url':e['url'],**metrics}
                db.execute('INSERT INTO link VALUES(?,?,?,?)',(event_id,e['source'],e['event_id'],json.dumps(candidate)))
    db.commit()
    reused={(s,r) for s,r in db.execute('SELECT source,remote_id FROM link GROUP BY source,remote_id HAVING count(DISTINCT local_id)>1')}
    counts=Counter();coverage=Counter();new_by_source=Counter()
    with (out/'enriched_catalog.csv').open('w',newline='') as f,(out/'mw_candidates.jsonl').open('w') as candidates_file:
        writer=csv.DictWriter(f,fieldnames=columns+EXTRA);writer.writeheader()
        for event_id,t,raw in db.execute('SELECT id,t,row FROM local ORDER BY t'):
            row=json.loads(raw);candidates=[];blocked=False
            for (encoded,) in db.execute('SELECT candidate FROM link WHERE local_id=?',(event_id,)):
                c=json.loads(encoded);c['reused_external_event']=(c['source'],c['event_id']) in reused
                candidates_file.write(json.dumps(c)+'\n')
                if c['reused_external_event'] and c['match']!='event_id':blocked=True
                else:candidates.append(c)
            errors=[];searched=[]
            for s in ['usgs','isc']:
                states=db.execute('SELECT status,error FROM batch WHERE source=? AND start<=? AND end>=?',(s,t,t)).fetchall()
                if states and all(x[0]=='ok' for x in states):searched.append(s)
                else:errors.append(s+': '+('; '.join(x[1] for x in states if x[1]) or 'not searched'))
            for s,state in source_state.items():
                if state.startswith('searched'):
                    if s=='global_cmt' and not timestamp('1976-01-01')<=t<timestamp('2026-01-01'):
                        errors.append('global_cmt: outside downloaded catalog coverage')
                    else:searched.append(s)
                else:errors.append(s+': '+state)
            result=dict.fromkeys(EXTRA,'')
            original=finite_number(row['magnitude'])
            if row['magnitude_type'].strip().lower() in MW_TYPES and original is not None:
                result.update(reported_mw=original,reported_mw_type=row['magnitude_type'],reported_mw_source='input_usgs',mw_lookup_status='already_reported_mw')
            else:
                chosen,status=choose(candidates)
                if chosen:
                    m=chosen['magnitude']
                    result.update(reported_mw=m['value'],reported_mw_type=m['type'],reported_mw_source=chosen['source'],
                                  reported_mw_agency=m['agency'],reported_mw_uncertainty=m['uncertainty'] if m['uncertainty'] is not None else '',
                                  matched_event_id=chosen['event_id'],match_dt_seconds=chosen['dt_seconds'],match_distance_km=chosen['distance_km'])
                    new_by_source[chosen['source']]+=1
                elif blocked:status='ambiguous_external_event_reuse'
                elif status=='no_direct_mw_found' and errors:status='no_mw_found_search_incomplete'
                result['mw_lookup_status']=status
            result['mw_lookup_sources']=';'.join(searched);result['mw_lookup_errors']=';'.join(errors)
            counts[result['mw_lookup_status']]+=1
            if errors:coverage['events_with_incomplete_source_coverage']+=1
            writer.writerow({**row,**result})
    with (out/'enriched_catalog.csv').open(newline='') as f, (out/'needs_review.csv').open('w',newline='') as review_file:
        reader=csv.DictReader(f)
        review_writer=csv.DictWriter(review_file,fieldnames=reader.fieldnames)
        review_writer.writeheader()
        for row in reader:
            if 'ambiguous' in row['mw_lookup_status'] or 'conflicting' in row['mw_lookup_status']:
                review_writer.writerow(row)
    total_mw=counts['already_reported_mw']+counts['matched_reported_mw']
    summary={'input_events':n,'events_with_reported_mw':total_mw,
             'reported_mw_percent':round(100*total_mw/n,4),'excluded_by_date':excluded,'statuses':dict(counts),
             'new_mw_by_source':dict(new_by_source),'source_coverage':source_state,**coverage,
             'failed_batches':[dict(source=s,start=iso(a),end=iso(b),error=e) for s,a,b,e in db.execute("SELECT source,start,end,error FROM batch WHERE status='error'")],
             'finished_utc':iso(datetime.now(timezone.utc).timestamp()),
             'note':'Automatic time/location matches are candidates, not definitive identity. No converted Mw used.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (out/'RUNNING').unlink();db.close()
    print(json.dumps(summary,indent=2),flush=True)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cache',type=Path,default=Path('data_cache/http'))
    p.add_argument('--cmt-file',type=Path);p.add_argument('--isc-gem',type=Path)
    p.add_argument('--bounds',nargs=4,type=float,metavar=('SOUTH','NORTH','WEST','EAST'))
    p.add_argument('--start');p.add_argument('--end',help='Exclusive UTC end date')
    p.add_argument('--seconds',type=float,default=60);p.add_argument('--km',type=float,default=100)
    p.add_argument('--depth-km',type=float,default=100);p.add_argument('--timeout',type=float,default=45)
    p.add_argument('--offline',action='store_true')
    return p

if __name__=='__main__':
    args=parser().parse_args()
    for value in [args.seconds,args.km,args.depth_km,args.timeout]:
        if finite_number(value) is None or value<=0:raise ValueError('Matching limits and timeout must be finite and positive')
    build(args)
