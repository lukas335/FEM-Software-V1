"""Pressure-relative stiffness metrics, without altering stored solver results."""
from math import isfinite


def geometry_key(case):
    shell = case['parameters']['measurement_shell']
    tx = shell.get('thickness_x_mm', shell.get('thickness_mm'))
    return shell['radius_mm'], tx, shell.get('thickness_y_mm', tx)


def stiffness(case):
    if case.get('status') != 'success' or not case.get('result'):
        return None
    value = case['result'].get('stiffness')
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def relative_stiffness(current, reference):
    if current is None or reference is None or reference <= 0:
        return None
    value = 100.0 * (current - reference) / reference
    return value if isfinite(value) else None


def stiffness_groups(cases, config):
    """Use planned pressure endpoints, never replace a failed baseline by another pressure."""
    planned = sorted(set(config.pressures))
    groups = {}
    for case in cases:
        groups.setdefault(geometry_key(case), []).append(case)
    provisional = any(c.get('status') in ('pending', 'running') for c in cases)
    output = {}
    for key, group in groups.items():
        by_pressure = {}
        for case in group:
            by_pressure.setdefault(case['parameters']['pressure_mmhg'], []).append(case)
        values = {p: stiffness(entries[0]) if len(entries) == 1 else None for p, entries in by_pressure.items()}
        p_ref, p_end = planned[0], planned[-1]
        k_ref, k_end = values.get(p_ref), values.get(p_end)
        change = relative_stiffness(k_end, k_ref)
        good = sum(values.get(p) is not None for p in planned)
        complete = good == len(planned)
        reasons = []
        if k_ref is None:
            reasons.append(f'Basisdruck {p_ref:g} mmHg: kein eindeutiges gültiges Ergebnis')
        elif k_ref <= 0:
            reasons.append('Basissteifigkeit nicht positiv; relative Änderung nicht auswertbar')
        if k_end is None and p_end != p_ref:
            reasons.append(f'Enddruck {p_end:g} mmHg: kein eindeutiges gültiges Ergebnis')
        warnings = list(dict.fromkeys(w for c in group if c.get('status') == 'success'
                                     for w in (c.get('result') or {}).get('warnings', [])))
        status = 'Vollständig' if complete else f'Unvollständig ({good}/{len(planned)})'
        if change is None:
            status += ' · relative Änderung nicht auswertbar'
        elif len(planned) == 1:
            status += ' · nur ein Druckwert'
        if warnings:
            status += ' · Hinweise prüfen'
        if provisional:
            status = 'Vorläufig · ' + status
        output[key] = dict(cases=group, values=values, reference_pressure_mmhg=p_ref,
                           comparison_pressure_mmhg=p_end, reference_stiffness=k_ref,
                           comparison_stiffness=k_end, relative_stiffness_change_pct=change,
                           complete=complete, provisional=provisional, successful=good,
                           status=status, notes='; '.join(reasons + warnings))
    return output
