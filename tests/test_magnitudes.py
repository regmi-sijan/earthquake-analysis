import csv
import tempfile
import unittest
from pathlib import Path
from earthquake_analysis.magnitudes import convert_magnitude
from convert_magnitudes import run

class MagnitudeTests(unittest.TestCase):
    def convert(self, kind, mag, depth=10):
        return convert_magnitude(dict(magnitude_type=kind, magnitude=mag, depth_km=depth))

    def test_published_examples(self):
        for kind, mag, expected, sigma in [('mb',5,5.28,.29),('ms',5,5.42,.17),('ms',7,7.01,.20)]:
            result=self.convert(kind,mag)
            self.assertAlmostEqual(result['mw_estimate'],expected)
            self.assertEqual(result['mw_conversion_sigma'],sigma)

    def test_boundaries_and_gap(self):
        for kind,mag in [('mb',3.5),('mb',6.2),('ms',3),('ms',6.1),('ms',6.2),('ms',8.2)]:
            self.assertEqual(self.convert(kind,mag)['mw_status'],'converted_mw')
        for kind,mag in [('mb',3.49),('mb',6.21),('ms',6.15),('ms',8.21)]:
            self.assertEqual(self.convert(kind,mag)['mw_status'],'outside_calibration_range')

    def test_no_guessed_conversions(self):
        for kind in ['ml','md','mc','mh','mB','mblg','mwp','ms_20']:
            self.assertEqual(self.convert(kind,5)['mw_estimate'],'')
        for value in ['',None,'nan','inf','bad']:
            self.assertEqual(self.convert('mb',value)['mw_status'],'missing_or_invalid_magnitude')
        for depth in [None,61,-1]:
            self.assertEqual(self.convert('ms',5,depth)['mw_status'],'ms_depth_not_eligible')

    def test_mw_preserved_unknown_uncertainty(self):
        for kind in ['mw','mww','mwc','mwb','mwr']:
            result=self.convert(kind,6.4)
            self.assertEqual(result['mw_estimate'],6.4)
            self.assertEqual(result['mw_conversion_sigma'],'')

    def test_pipeline_preserves_inputs_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'input.csv'; out=Path(tmp)/'result'
            content='id,magnitude,magnitude_type,depth_km\na,5,mb,10\nb,3,ml,4\n'
            src.write_text(content)
            result=run(src,out)
            self.assertEqual(result['mw_available_percent'],50)
            self.assertEqual(src.read_text(),content)
            with (out/'catalog_with_mw.csv').open() as f: rows=list(csv.DictReader(f))
            self.assertEqual(rows[0]['magnitude'],'5')
            self.assertEqual(rows[1]['mw_estimate'],'')
            with self.assertRaises(FileExistsError):run(src,out)

    def test_duplicate_fails_visibly(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'input.csv';out=Path(tmp)/'result'
            src.write_text('id,magnitude,magnitude_type\na,5,mw\na,6,mw\n')
            with self.assertRaises(ValueError):run(src,out)
            self.assertTrue((out/'FAILED.txt').exists())

if __name__ == '__main__':unittest.main()
