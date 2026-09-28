import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from earthquake_analysis.enrichment import (Cache, quakeml, ndk, choose, match_metrics,
                                           timestamp, normalized_iscgem, fetch_window)

XML=b'''<q:quakeml xmlns:q="http://quakeml.org/xmlns/quakeml/1.2" xmlns="http://quakeml.org/xmlns/bed/1.2" xmlns:cat="http://anss.org/xmlns/catalog/0.1"><eventParameters><event publicID="quakeml:us.anss.org/event/abc" cat:eventsource="us" cat:eventid="abc"><preferredOriginID>o2</preferredOriginID><preferredMagnitudeID>m2</preferredMagnitudeID><origin publicID="o1"><time><value>2015-04-25T06:11:26Z</value></time><latitude><value>0</value></latitude><longitude><value>0</value></longitude></origin><origin publicID="o2"><time><value>2015-04-25T06:11:26Z</value></time><latitude><value>28</value></latitude><longitude><value>85</value></longitude><depth><value>15000</value></depth></origin><magnitude publicID="m1"><type>mb</type><mag><value>6.5</value></mag></magnitude><magnitude publicID="m2"><type>Mww</type><mag><value>7.8</value><uncertainty>0.1</uncertainty></mag><creationInfo><agencyID>US</agencyID></creationInfo></magnitude></event></eventParameters></q:quakeml>'''
NDK=b'''PDE  2005/01/01 01:20:05.4  13.78  -88.78 193.1 5.0 0.0 EL SALVADOR
C200501010120A   B:  4    4  40 S: 27   33  50 M:  0    0   0 CMT: 1 TRIHD:  0.6
CENTROID:     -0.3 0.9  13.76 0.06  -89.08 0.09 162.8 12.5 FREE S-20050322125201
23  0.838 0.201 -0.005 0.231 -0.833 0.270  1.050 0.121 -0.369 0.161  0.044 0.240
V10   1.581 56  12  -0.537 23 140  -1.044 24 241   1.312   9 29  142 133 72   66
'''

class EnrichmentTests(unittest.TestCase):
    def test_xml_all_magnitudes_and_preferred_origin(self):
        e=quakeml(XML,'usgs','url')[0]
        self.assertEqual(e['latitude'],28)
        self.assertEqual(e['depth'],15)
        self.assertEqual(e['aliases'],['usabc'])
        self.assertEqual(len(e['magnitudes']),1)
        self.assertEqual(e['magnitudes'][0]['value'],7.8)
        self.assertEqual(e['magnitudes'][0]['uncertainty'],.1)
        self.assertTrue(e['magnitudes'][0]['preferred'])

    def test_invalid_response_not_no_results(self):
        with self.assertRaises(ValueError):quakeml(b'<html>Error</html>','isc','url')
        self.assertEqual(quakeml(b'','isc','url'),[])

    def test_ndk_uses_reference_not_centroid(self):
        e=ndk(gzip.compress(NDK))[0]
        self.assertEqual(e['latitude'],13.78)
        self.assertEqual(e['depth'],193.1)
        self.assertAlmostEqual(e['magnitudes'][0]['value'],4.6786,places=3)
        with self.assertRaises(ValueError):ndk(NDK+b'bad\n')

    def test_match_distance_depth_time_and_dateline(self):
        row={'id':'x','time_utc':'2015-01-01T00:00:00Z','latitude':0,'longitude':179.9,'depth_km':10}
        e={'time':timestamp(row['time_utc'])+30,'latitude':0,'longitude':-179.9,'depth':20,'aliases':[],'source':'isc'}
        self.assertIsNotNone(match_metrics(row,e))
        self.assertIsNone(match_metrics(row,{**e,'time':e['time']+60}))
        self.assertIsNone(match_metrics(row,{**e,'depth':500}))
        self.assertIsNone(match_metrics(row,{**e,'latitude':20}))

    def test_ambiguity_and_converted_not_direct(self):
        c={'source':'isc','event_id':'a','match':'time_location','magnitude':{'basis':'reported_mw','value':5.,'type':'mw','preferred':True,'agency':'X','magnitude_id':'m'}}
        self.assertEqual(choose([c])[1],'matched_reported_mw')
        self.assertEqual(choose([c,{**c,'event_id':'b'}])[1],'ambiguous_event_match')
        conflict={**c,'magnitude':{**c['magnitude'],'value':6}}
        self.assertEqual(choose([c,conflict])[1],'conflicting_mw_estimates')
        proxy={**c,'magnitude':{**c['magnitude'],'basis':'converted'}}
        self.assertIsNone(choose([proxy])[0])

    def test_cache_offline_miss(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):Cache(d,offline=True).get('https://example.org')

    def test_iscgem_requires_explicit_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'gem.csv';p.write_text('event_id,time_utc,latitude,longitude,depth_km,mw,mw_basis,reference\na,2000-01-01,0,0,10,5,converted,doi\n')
            e=normalized_iscgem(p)[0]
            self.assertEqual(e['magnitudes'][0]['basis'],'converted')

    def test_timestamp_offset(self):
        self.assertEqual(timestamp('2015-01-01T05:45:00+05:45'),
                         timestamp('2015-01-01T00:00:00Z'))

    def test_failed_query_remains_incomplete(self):
        from enrich_mw import build,parser
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'input.csv';cmt=root/'cmt.ndk';out=root/'out'
            cmt.write_bytes(NDK)
            p.write_text('id,time_utc,latitude,longitude,depth_km,magnitude,magnitude_type\nx,2015-04-25T06:11:26Z,28,85,15,4,mb\n')
            args=parser().parse_args(['--input',str(p),'--output',str(out),'--cmt-file',str(cmt),'--cache',str(root/'cache'),'--offline'])
            with patch('enrich_mw.fetch_window',side_effect=RuntimeError('service unavailable')):
                build(args)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(len(summary['failed_batches']),2)
            self.assertEqual(summary['statuses'],{'no_mw_found_search_incomplete':1})

    def test_external_event_reuse_requires_review(self):
        from enrich_mw import build,parser
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'input.csv';cmt=root/'cmt.ndk';out=root/'out'
            cmt.write_bytes(NDK)
            p.write_text('id,time_utc,latitude,longitude,depth_km,magnitude,magnitude_type\na,2015-04-25T06:11:26Z,28,85,15,4,mb\nb,2015-04-25T06:11:35Z,28,85,15,4,mb\n')
            args=parser().parse_args(['--input',str(p),'--output',str(out),'--cmt-file',str(cmt),'--cache',str(root/'cache'),'--offline'])
            with patch('enrich_mw.fetch_window',side_effect=lambda cache,source,*rest: quakeml(XML,source,'url')):
                build(args)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['statuses'],{'ambiguous_external_event_reuse':2})
            self.assertEqual(summary['events_with_reported_mw'],0)

    def test_end_to_end_offline_and_resume(self):
        from enrich_mw import build,parser
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'input.csv';cmt=root/'cmt.ndk';out=root/'out'
            cmt.write_bytes(NDK)
            p.write_text('id,time_utc,latitude,longitude,depth_km,magnitude,magnitude_type\nusabc,2015-04-25T06:11:26Z,28,85,15,6.5,mb\n')
            args=parser().parse_args(['--input',str(p),'--output',str(out),'--cmt-file',str(cmt),'--cache',str(root/'cache'),'--offline'])
            with patch('enrich_mw.fetch_window',return_value=quakeml(XML,'usgs','url')) as fetch:
                build(args)
                self.assertEqual(fetch.call_count,2)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['new_mw_by_source'],{'usgs':1})
            with patch('enrich_mw.fetch_window',side_effect=AssertionError('Should resume cached batches')):
                build(args)
            self.assertFalse((out/'RUNNING').exists())

if __name__=='__main__':unittest.main()
