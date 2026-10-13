"""Read-only, flat whole-study tables for CSV export and spreadsheet paste."""
from dataclasses import dataclass
import csv
import io
from math import isfinite

from model.study_metrics import geometry_key, relative_stiffness, stiffness, stiffness_groups


@dataclass
class StudyTable:
    headers: list[str]
    rows: list[list]


def study_table(cases, config, kind='summary'):
    groups = stiffness_groups(cases, config)
    pressure = 'Anfangsdruck P0' if config.pressure_mode == 'closed' else 'Druck'
    if kind == 'summary':
        headers = ['Relative Steifigkeitsänderung [%]', 'R [mm]', 'tx [mm]', 'ty [mm]',
                   f'{pressure} Referenz [mmHg]', f'{pressure} Vergleich [mmHg]',
                   'k Referenz [N/mm]', 'k Vergleich [N/mm]', 'Erfolgreiche Druckfälle',
                   'Geplante Druckfälle', 'Referenzgeometrie', 'Status', 'Hinweis']
        for p in sorted(set(config.pressures)):
            headers.append(f'k bei {p:g} mmHg [N/mm]')
        rows = []
        for key, group in groups.items():
            rows.append([group['relative_stiffness_change_pct'], *key,
                         group['reference_pressure_mmhg'], group['comparison_pressure_mmhg'],
                         group['reference_stiffness'], group['comparison_stiffness'],
                         group['successful'], len(set(config.pressures)),
                         '; '.join(sorted({name for c in group['cases'] for name in c.get('references', [])})),
                         group['status'], group['notes'],
                         *(group['values'].get(p) for p in sorted(set(config.pressures)))])
        rows.sort(key=lambda row: (row[0] is None, -(row[0] or 0), *row[1:4]))
        return StudyTable(headers, rows)
    if kind != 'cases':
        raise ValueError('Unbekannte Exporttabelle.')
    headers = ['Relative Steifigkeitsänderung zur Basis [%]', 'R [mm]', 'tx [mm]', 'ty [mm]',
               f'{pressure} [mmHg]', f'{pressure} Referenz [mmHg]', 'k [N/mm]', 'k Referenz [N/mm]',
               'ΔF Endpunkte [N]', 'Δx Endpunkte [mm]', 'Kontaktweg [mm]',
               'Zielkraft [N]', 'Erreichte Kraft [N]', 'Endweg bei Zielkraft [mm]',
               'Status', 'Hinweis', 'Referenzgeometrie', 'Rasterfall', 'Fall-ID',
               'Druckmodus', 'Zustellsteuerung', 'Material Messschale', 'Material Druckschale',
               'R Druckschale [mm]', 't Druckschale [mm]']
    rows = []
    for case in sorted(cases, key=lambda c: (*geometry_key(c), c['parameters']['pressure_mmhg'], c['id'])):
        key = geometry_key(case); group = groups[key]; params = case['parameters']
        k = stiffness(case)
        result = (case.get('result') or {}) if case.get('status') == 'success' else {}
        note = '; '.join(filter(None, (case.get('error', ''), '; '.join(result.get('warnings', [])), group['notes'])))
        rows.append([relative_stiffness(k, group['reference_stiffness']), *key,
                     params['pressure_mmhg'], group['reference_pressure_mmhg'], k, group['reference_stiffness'],
                     result.get('delta_force_n'), result.get('delta_displacement_mm'), result.get('contact_mm'),
                     result.get('target_force_n'), result.get('achieved_force_n'), result.get('achieved_drive_mm'),
                     case.get('status'), note, '; '.join(case.get('references', [])), case.get('in_grid', False),
                     case['id'], config.pressure_mode, params.get('drive_control', 'displacement'),
                     params['measurement_shell']['material_name'], params['pressure_shell']['material_name'],
                     params['pressure_shell']['radius_mm'], params['pressure_shell']['thickness_mm']])
    return StudyTable(headers, rows)


def table_text(table, *, delimiter=';', decimal_comma=True):
    """Excel-compatible numbers, explicit empty missing values, no formula injection."""
    def cell(value):
        if value is None: return ''
        if isinstance(value, bool): return 'Ja' if value else 'Nein'
        if isinstance(value, (int, float)):
            if not isfinite(value): return ''
            text = str(value)
            return text.replace('.', ',') if decimal_comma else text
        text = str(value)
        if text.lstrip().startswith(('=', '+', '-', '@')):
            text = "'" + text
        return text
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator='\r\n')
    writer.writerow(table.headers)
    writer.writerows([cell(value) for value in row] for row in table.rows)
    return buffer.getvalue()
