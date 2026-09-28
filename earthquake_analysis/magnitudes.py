"""Conservative magnitude homogenization; see README for scientific limitations."""
import math

SOURCE = 'https://doi.org/10.1007/s10950-006-9012-4'
MW_TYPES = {'mw', 'mww', 'mwc', 'mwb', 'mwr'}
FIELDS = ['mw_estimate', 'mw_conversion_sigma', 'mw_status', 'mw_method', 'mw_reference']


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def convert_magnitude(row):
    """Return new fields only. Sigma is regression scatter, not total error.

    Ms conversions require known depth <=60 km (conservative shallow-event
    policy). No interpolation across 6.1 < Ms < 6.2, and no extrapolation.
    Reported Mw uncertainty is unknown, not zero. Exact mb is supported;
    mB and regional mb_Lg are deliberately not treated as mb.
    """
    result = dict.fromkeys(FIELDS, '')
    raw_type = (row.get('magnitude_type') or '').strip()
    kind = raw_type.lower()
    magnitude = finite_number(row.get('magnitude'))
    if magnitude is None:
        result['mw_status'] = 'missing_or_invalid_magnitude'
        return result
    if kind in MW_TYPES:
        result.update(mw_estimate=magnitude, mw_status='reported_mw',
                      mw_method='reported_' + kind)
        return result
    if not kind or kind in {'no', 'unknown', 'm'}:
        result['mw_status'] = 'missing_or_ambiguous_type'
        return result
    if raw_type not in {'mb', 'ms', 'Ms', 'MS'}:
        result['mw_status'] = 'requires_calibration'
        return result
    if raw_type == 'mb':
        if not 3.5 <= magnitude <= 6.2:
            result['mw_status'] = 'outside_calibration_range'
            return result
        slope, intercept, sigma, method = .85, 1.03, .29, 'scordilis_2006_mb'
    else:
        if 3.0 <= magnitude <= 6.1:
            slope, intercept, sigma, method = .67, 2.07, .17, 'scordilis_2006_ms_low'
        elif 6.2 <= magnitude <= 8.2:
            slope, intercept, sigma, method = .99, .08, .20, 'scordilis_2006_ms_high'
        else:
            result['mw_status'] = 'outside_calibration_range'
            return result
        depth = finite_number(row.get('depth_km'))
        if depth is None or not 0 <= depth <= 60:
            result['mw_status'] = 'ms_depth_not_eligible'
            return result
    result.update(mw_estimate=round(slope*magnitude + intercept, 6),
                  mw_conversion_sigma=sigma, mw_status='converted_mw',
                  mw_method=method, mw_reference=SOURCE)
    return result
