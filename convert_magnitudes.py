#!/usr/bin/env python3
"""Stream CSV files to a separate, audited Mw-equivalent catalogue."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from earthquake_analysis.magnitudes import FIELDS, convert_magnitude


def run(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    files = sorted(source.rglob('earthquakes.csv')) if source.is_dir() else [source]
    if not files or any(not f.is_file() for f in files):
        raise ValueError('Input must be a CSV or a catalog directory containing earthquakes.csv files')
    # Validate schema before creating output. Preserve all original fields.
    columns = None
    for path in files:
        with path.open(newline='', encoding='utf-8-sig') as stream:
            header = next(csv.reader(stream), [])
        if not {'id', 'magnitude', 'magnitude_type'} <= set(header):
            raise ValueError(f'Missing required columns in {path}')
        if set(header) & set(FIELDS):
            raise ValueError(f'Input already contains conversion fields: {path}')
        if columns is None:
            columns = header
        elif header != columns:
            raise ValueError(f'Inconsistent CSV schema: {path}')
    output.mkdir(parents=True, exist_ok=False)
    statuses, breakdown, seen = Counter(), Counter(), set()
    duplicates = 0
    manifest = []
    try:
        with (output/'catalog_with_mw.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns+FIELDS)
            writer.writeheader()
            for i, path in enumerate(files, 1):
                h = hashlib.sha256()
                with path.open('rb') as raw:
                    for chunk in iter(lambda: raw.read(1024*1024), b''):
                        h.update(chunk)
                manifest.append({'path': str(path), 'sha256': h.hexdigest()})
                with path.open(newline='', encoding='utf-8-sig') as raw:
                    for row in csv.DictReader(raw):
                        if None in row or any(v is None for v in row.values()):
                            raise ValueError(f'Malformed CSV row in {path}')
                        event_id = row['id'].strip()
                        if not event_id:
                            raise ValueError(f'Missing event ID in {path}')
                        # Fail on duplicates: do not silently choose between conflicting versions.
                        if event_id in seen:
                            duplicates += 1
                            raise ValueError(f'Duplicate event ID {event_id}; resolve source versions first')
                        seen.add(event_id)
                        converted = convert_magnitude(row)
                        statuses[converted['mw_status']] += 1
                        breakdown[(row['magnitude_type'] or '(missing)', converted['mw_status'])] += 1
                        writer.writerow({**row, **converted})
                if i % 250 == 0:
                    print(f'Processed {i}/{len(files)} files', flush=True)
        total = sum(statuses.values())
        summary = {
            'completed_utc': datetime.now(timezone.utc).isoformat(),
            'input': str(source), 'total_unique_events': total,
            'status_counts': dict(statuses),
            'mw_available_percent': 100*(statuses['reported_mw']+statuses['converted_mw'])/total if total else 0,
            'duplicate_policy': 'fail; require explicit reconciliation',
            'conversion': 'Scordilis 2006 mb and Ms; Ms depth 0–60 km',
            'sigma_meaning': 'Published regression residual scatter only; not total predictive uncertainty',
            'limitations': 'No regional validation, completeness assessment, magnitude filtering, or declustering performed',
            'source_files': manifest,
        }
        (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        with (output/'conversion_breakdown.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['original_type', 'status', 'count', 'percent_of_all_events'])
            for (kind, status), count in sorted(breakdown.items()):
                writer.writerow([kind, status, count, round(100*count/total, 6)])
        print(json.dumps({k:v for k,v in summary.items() if k!='source_files'}, indent=2))
        return summary
    except Exception as error:
        (output/'FAILED.txt').write_text(f'Incomplete output; do not use.\n{error}\n')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New directory; existing directories are rejected')
    args = parser.parse_args()
    run(args.input, args.output)
