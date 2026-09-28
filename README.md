# Earthquake magnitude conversion

Python 3 standard library only; no installation required. Source catalogs are read-only.

## Run

```sh
python3 convert_magnitudes.py --input /path/to/catalog_directory --output analysis_outputs/global_new_run
python3 convert_magnitudes.py --input /path/to/kathmandu.csv --output analysis_outputs/kathmandu_new_run
python3 -m unittest discover -s tests -v
```

Directory input searches only for `earthquakes.csv`. File input accepts one CSV.
Output directories must be new. Duplicate IDs fail explicitly instead of choosing
between potentially conflicting versions. An interrupted or failed run must not
be used: only runs with a summary.json and without FAILED.txt are complete.

## Methods

Target: Mw-equivalent estimates. Original columns and values are retained.

| Input | Estimate | Valid input magnitude | Regression scatter |
|---|---|---|---|
| mw, mww, mwc, mwb, mwr | Retain reported value | Finite reported value | Unknown (blank) |
| mb | 0.85 mb + 1.03 | 3.5–6.2 inclusive | 0.29 |
| ms / Ms / MS | 0.67 Ms + 2.07 | 3.0–6.1 inclusive | 0.17 |
| ms / Ms / MS | 0.99 Ms + 0.08 | 6.2–8.2 inclusive | 0.20 |

The Ms policy additionally requires known depth from 0 to 60 km; this is a
conservative shallow-event eligibility rule, not a fitted depth correction.
No extrapolation or interpolation across the 6.1–6.2 gap is performed.
`mB` is distinct from `mb`; regional `mblg` is not treated as `mb`.
Other types (including ML, Md, Mc, Mh, mwp, ms_20) are retained but receive no
estimate until their methods and applicable calibration are established.
No global ML/Md coefficients are invented or borrowed from unrelated regions.

Sources:
- Scordilis (2006), https://doi.org/10.1007/s10950-006-9012-4
- Equations and ranges independently checked in the methods of Bungum et al.
  (2024), section 3.2: https://doi.org/10.1007/s10950-024-10270-z
- Reported residual scatter is tabulated in the research literature, including
  https://geos.cicese.mx/index.php/geos/article/view/58/55
- USGS magnitude definitions: https://www.usgs.gov/programs/earthquake-hazards/magnitude-types

`mw_conversion_sigma` is the published regression residual scatter in magnitude
units. It is NOT a calibrated event-specific total uncertainty, nor does it
include regional bias or all coefficient/input uncertainty. Blank uncertainty
for reported Mw does not mean zero uncertainty. These global empirical estimates
have not yet been validated against paired magnitudes for Kathmandu.

## Outputs

- `catalog_with_mw.csv`: every input event with estimate, scatter, status, method,
  and citation appended. Unsupported and invalid records are not silently dropped.
- `conversion_breakdown.csv`: counts by original magnitude type and outcome.
- `summary.json`: settings, counts, limitations, and input paths/SHA-256 hashes.

The resulting catalog is only partially homogenized. Do not substitute the raw
magnitude when mw_estimate is blank and then call the result an Mw catalog.
No magnitude cutoff, completeness assessment, mainshock identification, or
waiting-time analysis is performed here. Missing estimates can bias later
statistics; assess coverage before using a threshold such as Mw >= 5.

Code lives in the Documents/ChatGPT/Earthquake workspace. The existing Desktop
catalogs remain the inputs; no source records are changed.

## Earthquake databases and additional Mw estimates

These sources let you search for recorded earthquakes and, where available,
retrieve additional magnitude estimates. No catalog contains every earthquake,
and no source provides directly determined Mw for every recorded event.
Coverage varies with time, region, and earthquake size.

| Source | Links | Information available |
|---|---|---|
| USGS Earthquake Catalog / ComCat | [Interactive search](https://earthquake.usgs.gov/earthquakes/search/) · [API documentation](https://earthquake.usgs.gov/fdsnws/event/1/) | Event IDs, UTC times, locations, depths, preferred magnitudes, and additional origins/magnitudes and moment-tensor information where available |
| Global Centroid Moment Tensor (Global CMT) | [Project website](https://www.globalcmt.org/) · [Catalog search](https://www.globalcmt.org/CMTsearch) | Moment-tensor solutions and moment magnitude for moderate-to-large earthquakes since 1976; search by time, location, depth, and magnitude |
| International Seismological Centre (ISC) Bulletin | [Bulletin](https://www.isc.ac.uk/iscbulletin/) · [Web services](https://www.isc.ac.uk/iscbulletin/search/webservices/) | Event solutions and magnitude estimates contributed by multiple agencies; useful for finding alternatives missing from the local CSV |
| ISC-GEM Global Instrumental Earthquake Catalogue | [Overview](https://www.isc.ac.uk/iscgem/overview.php) · [Downloads](https://www.isc.ac.uk/iscgem/download.php) | Curated global catalog emphasizing larger earthquakes, with directly determined or converted Mw; inspect magnitude provenance before treating an entry as direct Mw |

### Finding events and downloading records

1. **Start with USGS.** Set an explicit date interval and select earthquake event
   type. For global searches, omit geographic restrictions. For Kathmandu, use
   a circle centered on latitude 27.7017, longitude 85.3206, with radius 400 km.
   Use UTC consistently. Avoid a magnitude cutoff when the aim is to recover all
   available records in the selected region and period.
2. **Retrieve additional magnitudes.** The existing CSVs contain one selected
   magnitude per event. Request QuakeML (`format=quakeml`) with
   `includeallmagnitudes=true` to obtain additional estimates available in
   ComCat. A query using `eventid` also includes associated origins, magnitudes,
   and moment-tensor/focal-mechanism information where available. This does not
   guarantee that Mw exists for that event.
3. **Use bounded batches.** For large USGS downloads, split requests by time
   (and subdivide dense intervals) to stay within the 20,000-event query limit.
   Deduplicate overlapping interval boundaries by event ID, explicitly
   reconcile differing versions, cache responses, and retry transient failures.
4. **Supplement from Global CMT and ISC.** Use their searches or documented
   downloads/web services. Match catalog identifiers when a verified crosswalk
   exists; otherwise compare origin time, location, and depth and review
   ambiguous candidates. Do not match by magnitude alone. Global CMT centroid
   times/locations can differ from earthquake origin times/locations.
5. **Preserve provenance.** Keep every candidate's source catalog, event ID,
   agency, magnitude type/value, uncertainty if supplied, and retrieval date.
   Select a preferred direct Mw with a documented quality rule, not by taking
   the largest candidate. Use empirical conversion only when a suitable direct
   Mw cannot be recovered. Do not count different agency solutions as separate
   earthquakes.

Recommended order for this project: USGS additional magnitudes → Global CMT /
ISC supplementary lookup → existing conversion estimates as fallback. Start
with the 1,588 Kathmandu records before scaling up to the global catalog.
The conversion script currently reads local CSVs only; it does **not** implement
these online retrieval or cross-catalog matching steps.

## Retrieve additional reported Mw (USGS, Global CMT, ISC)

`enrich_mw.py` searches additional magnitudes and matches event records. It keeps
reported/scalar-moment Mw separate from the earlier empirical conversions. It
requires Python 3.10+ and the standard library; internet access is needed for a
new search. `earthquake_analysis/enrichment.py` contains the source readers and
matching rules.

### Run on the Kathmandu catalog

From this repository directory:

```sh
python3 enrich_mw.py \
  --input /Users/sijanregmi/Desktop/Shyam_Paper/EarthQuake/kathmandu/kathmandu_earthquakes_within_400_km.csv \
  --output analysis_outputs/mw_lookup_kathmandu_full \
  --cmt-file data_cache/global_cmt_1976_2025.ndk.gz \
  --bounds 22 34 78 93
```

The bounds are a query box (south, north, west, east), expanded beyond the input
region to allow matches near its edges. They do not change the input dataset.
If `--cmt-file` is omitted, the published 1976–2025 Global CMT NDK archive is
fetched automatically. The pinned archive does not cover 1973–1975 or 2026;
those dates are explicitly marked as outside its coverage. Optional pre-1976
CMT archives are not included in this version.

Use `--start 2015-01-01 --end 2016-01-01` with a different output directory for
a small test (end date is exclusive). To process the global catalog, set
`--input` to its directory and **omit `--bounds`**. The global run uses monthly
queries and can download substantial data; the regional run uses yearly queries.
No magnitude filter is applied to the lookup requests.

Repeat the exact command to resume. Successful source batches are reused;
failed batches are retried. `--offline` permits cached responses only. Use a new
output directory for different input or matching settings. SQLite indexes keep
matching efficient without loading millions of local rows into memory.
Do not run two processes against the same output directory at the same time.

### Selection and matching policy

- Retain a valid input Mw-family magnitude (`mw`, `mww`, `mwc`, `mwb`, `mwr`).
- Read all available Mw-family estimates from USGS and ISC QuakeML.
- Derive Global CMT Mw from its scalar seismic moment using its documented
  formula: Mw = (2/3)(log10(M0 in dyne-cm) - 16.1). This is a moment-based
  determination, not an mb/Ms regression.
- Prefer a USGS event-ID association; otherwise require origin times within
  **60 seconds**, epicenters within **100 km**, and depth difference within
  **100 km** when both depths exist. Missing depth does not exclude a match.
  These are configurable heuristic tolerances, not calibrated probabilities.
- Use Global CMT's reference hypocenter time/location, not its centroid.
- Multiple matching event IDs within one source are ambiguous. An external
  event associated with multiple local events is not automatically assigned
  through time/location matching. Preserve candidates for review.
- Selection order is input USGS Mw, additional USGS Mw, Global CMT, ISC, then
  optional direct ISC-GEM Mw. This is an explicit default preference, not a
  guarantee that one agency is always more accurate.
- Within a source event, prefer its preferred Mw, then mww/mwc/mwb/mwr/mw,
  then agency and magnitude ID for deterministic ties. A spread greater than
  0.3 magnitude units within that event's candidate Mw estimates is flagged
  for review. No averaging and no selection by largest magnitude.
- Keep all candidates, including alternatives from lower-priority sources.
  Multiple agencies may be reporting the same underlying solution; candidates
  are not independent observations. Cross-source disagreements need review.

These automated matches are provisional event associations. Small historical
location/time errors or close aftershock sequences can cause missed or ambiguous
matches. Compare tolerance settings and inspect candidates before publication.

### ISC-GEM import

The [official ISC-GEM download form](https://www.isc.ac.uk/iscgem/request_catalogue.php)
requires registration and a CAPTCHA. This program does not submit personal data
or bypass that form. Without a supplied file it records ISC-GEM as **not searched**.

After obtaining the catalog, prepare a normalized CSV with these exact columns:

```text
event_id,time_utc,latitude,longitude,depth_km,mw,mw_basis,reference
```

Set `mw_basis` to `direct`, `converted`, or `unknown` using the downloaded
version's magnitude provenance documentation. Never infer direct Mw merely from
a column named Mw. Pass the normalized file with `--isc-gem /path/to/file.csv`.
All spatial/time candidates are retained, but only `direct` entries are eligible
for automatic reported-Mw selection. Preserve and cite the catalog version and
its CC-BY-SA license. Native ISC-GEM CSV normalization is not automated here.

### Enrichment outputs and interpretation

- `enriched_catalog.csv`: original fields plus selected reported Mw, type,
  source/agency, supplied uncertainty, match diagnostics, and search status.
- `mw_candidates.jsonl`: all Mw candidates, their provenance URLs, original
  magnitude IDs, match distances/time differences, and reuse flags.
- `summary.json`: results, failed batches, and source-coverage limitations.
- `settings.json`: input hashes, source version, selected scope, and tolerances.
- `index.sqlite`: resumable local/remote catalog and query indexes.
- `data_cache/http/`: raw responses and URL/retrieval-time/checksum metadata.

A `RUNNING` marker means the output is unfinished. A finished run may still have
source failures: always inspect `failed_batches` and per-event `mw_lookup_errors`.
`no_mw_found_search_incomplete` means the configured search was incomplete; it
must not be interpreted as proof that no Mw exists. Even a successful source
query only covers that source's currently published catalog. No mainshock
selection, empirical conversions, or magnitude thresholds are applied here.

Source interface references:
- [USGS FDSN event service](https://earthquake.usgs.gov/fdsnws/event/1/)
- [ISC FDSN event service](https://www.isc.ac.uk/fdsnws/event/1/)
- [Global CMT download page](https://www.globalcmt.org/CMTfiles.html)
- [Global CMT NDK format](https://www.ldeo.columbia.edu/~gcmt/projects/CMT/catalog/allorder.ndk_explained)

### Verified Kathmandu run

The completed 1,588-event Kathmandu run searched USGS, Global CMT, and ISC:

| Outcome | Events |
|---|---:|
| Mw already present in input | 65 |
| Additional Mw selected from USGS | 125 |
| Additional Mw selected from Global CMT | 25 |
| Additional Mw selected from ISC | 77 |
| Total with selected reported/moment-based Mw | **292 (18.39%)** |
| Ambiguous event reuse or conflicting Mw; review required | 5 |
| No selected Mw found; ISC-GEM still unsearched | 1,291 |

There were no failed USGS/ISC batches. All input CSV fields were verified
unchanged. Outputs are in `analysis_outputs/mw_lookup_kathmandu_full/`.
`needs_review.csv` isolates the five review cases; use `mw_candidates.jsonl`
for their full candidate evidence. The source hierarchy determines which source
gets credit above; a selected Mw can also be present in another catalog.
ISC-GEM registration/download remains outstanding. Selection does not guarantee
that a reported agency Mw was determined independently of every other estimate.

The code supports the full global catalog, but that large online run has **not**
been executed. To run it (resumable, no geographic or magnitude restriction):

```sh
python3 enrich_mw.py \
  --input /Users/sijanregmi/Desktop/Shyam_Paper/EarthQuake/usgs_weekly_catalog_1973_2025_20260830_052819 \
  --output analysis_outputs/mw_lookup_global_full \
  --cmt-file data_cache/global_cmt_1976_2025.ndk.gz
```

The output is a separate catalog: neither this lookup nor empirical conversion
modifies the Desktop source files. Run `python3 -m unittest discover -s tests -v`
for the conversion and enrichment tests, including failure/resume handling and
ambiguity checks.
