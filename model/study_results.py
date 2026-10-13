"""Contact-relative, path-weighted regression and pressure sensitivity."""

from pathlib import Path
import json
import re
from xml.etree import ElementTree as ET
import numpy as np

from model.results import ForceCurve, automatic_fit_range, read_force_curve
from model.study import StudyConfig
from model.study_metrics import stiffness_groups


def analyze_curve(curve: ForceCurve, config: StudyConfig, *, fit_bounds=None) -> dict:
    contact = automatic_fit_range(curve)
    ordered = sorted(zip(curve.displacement_mm, curve.force_n))
    x = np.asarray([point[0] for point in ordered]) - contact.contact_mm
    y = np.asarray([point[1] for point in ordered])
    # Each run owns its own first-contact and final-contact point.  A shared
    # fit window across pressures would silently change both delta values and
    # is not used for endpoint stiffness.
    a, b = contact.start_mm - contact.contact_mm, x[-1]
    if b <= a:
        raise ValueError("Kein gemeinsamer belasteter Fitbereich vorhanden.")
    if x[0] > a or x[-1] < b:
        raise ValueError(f"Endpunkt δ={b:g} mm nicht erreicht (verfügbar: {x[-1]:.4f} mm). Hub erhöhen.")
    indices = np.flatnonzero((x >= a) & (x <= b))
    if len(indices) < 2:
        raise ValueError("Mindestens zwei Endpunkte für die Steifigkeitsauswertung nötig.")
    first, last = int(indices[0]), int(indices[-1])
    delta_u = float(x[last] - x[first])
    delta_f = float(y[last] - y[first])
    if delta_u <= 0:
        raise ValueError("Die Endpunkt-Verschiebung muss zunehmen.")
    k = delta_f / delta_u
    warnings = []
    if k <= 0:
        warnings.append("Keine positive Steifigkeit")
    return {"stiffness": float(k), "intercept": None, "r_squared": None,
        "delta_force_n": delta_f, "delta_displacement_mm": delta_u,
        "first_contact_force_n": float(y[first]), "last_contact_force_n": float(y[last]),
        "count": 2,
        "contact_mm": contact.contact_mm, "contact_lower_mm": contact.lower_mm,
        "contact_upper_mm": contact.upper_mm, "fit_start_delta_mm": a, "fit_end_delta_mm": b,
        "max_force_n": float(y.max()), "warnings": warnings}


def check_preload(log_path: Path):
    """Require a contact-pressure record at every saved preload time."""
    # Successful runs may have their large FEBio log removed by the storage
    # cleanup.  The exported force, cavity and nodal histories are then the
    # authoritative post-processing inputs; absence of this optional
    # diagnostic log must not invalidate the completed result.
    if not log_path.is_file():
        return
    root = ET.parse(log_path.with_name("model.feb")).getroot()
    if root.find("./Output/logfile/face_data[@name='contact_state']") is None:
        from solver.accepted_history import read_records, check_separated_preload
        records = read_records(log_path)
        # Compact parameter-study runs intentionally omit nodal displacement
        # records. FEBio has already completed the preload/restart path; there
        # is no nodal history available for this optional geometric check.
        if not any(record.name == "nodal_displacement" for record in records):
            return
        check_separated_preload(log_path.with_name("model.feb"), records)
        return
    surface = root.find("./Mesh/Surface[@name='measurement_contact']")
    if surface is None or not len(surface):
        raise ValueError("Kontaktfläche fehlt im Eingabemodell.")
    expected_faces = set(range(1, len(surface) + 1))
    times, reactions = set(), set()
    for block in re.split(r"(?=Data Record\s*#\d+)", log_path.read_text(encoding="utf-8", errors="replace")):
        match = re.search(r"(?m)^\s*Time\s*=\s*([\d.eE+-]+)", block)
        if not match or float(match[1]) > 1 + 1e-8:
            continue
        time = float(match[1])
        if re.search(r"Data\s*=\s*[\"']?nodal_displacement\b", block):
            reactions.add(time)
        if not re.search(r"Data\s*=\s*[\"']?contact_state\b", block):
            continue
        rows = [line.split() for line in block.splitlines()]
        values = {int(row[0]): float(row[2]) for row in rows if len(row) == 3 and row[0].isdigit()}
        pressure = list(values.values())
        if set(values) != expected_faces or not np.isfinite(pressure).all():
            raise ValueError("Unvollständige Kontaktdaten im Druckaufbau.")
        if max(map(abs, pressure)) > 1e-10:  # MPa; numerical contact-pressure noise floor.
            raise ValueError("Kontaktvorlast während des Druckaufbaus: Anfangsspalt erhöhen.")
        times.add(time)
    if not reactions or not reactions.issubset(times):
        raise ValueError("Kontaktfreiheit im Druckaufbau nicht vollständig nachweisbar.")


def analyze_case(directory: Path, config: StudyConfig) -> dict:
    target = target_summary(directory, config)
    check_preload(directory / "solver.log")
    result = {**analyze_curve(read_force_curve(directory / "force_displacement.csv"), config), **target}
    result["warnings"].extend(cavity_warnings(directory))
    return result


def cavity_warnings(directory: Path) -> list[str]:
    path = directory / "case.json"
    if not path.is_file():
        return []
    metadata = json.loads(path.read_text(encoding="utf-8"))
    return metadata.get("cavity_results", {}).get("warnings", [])


def target_summary(directory: Path, config: StudyConfig) -> dict:
    p = config.base_parameters
    if p.drive_control != "target_force":
        return {}
    metadata = json.loads((directory / "case.json").read_text(encoding="utf-8"))
    curve = read_force_curve(directory / "force_displacement.csv")
    if (metadata.get("termination_reason") != "target_reached" or metadata.get("target_reached") is not True
            or abs(curve.force_n[-1] - p.target_force_n) > p.force_tolerance_n
            or curve.displacement_mm[-1] > p.max_displacement_mm + 1e-8):
        raise ValueError(f"Zielkraft nicht erreicht ({metadata.get('termination_reason', 'unbekannt')}); kein Ranking.")
    return {"target_force_n": p.target_force_n, "achieved_force_n": curve.force_n[-1],
            "achieved_drive_mm": curve.displacement_mm[-1], "termination_reason": "target_reached"}


def refresh_automatic_fits(cases, config, run_path):
    """Refit all verified contact curves over the same currently available window.

    Eligibility survives a temporarily invalid common fit (e.g. <5 samples).
    No solver runs or invented points; the final window is independent of run order.
    """
    if config.fit_mode != "automatic":
        return
    curves = []
    for case in cases:
        if not case.get("auto_fit_eligible"):
            continue
        try:
            target_summary(run_path(case["attempts"][-1]), config)
            curve = read_force_curve(run_path(case["attempts"][-1]) / "force_displacement.csv")
            contact = automatic_fit_range(curve)
            curves.append((case, curve, contact))
        except (OSError, ValueError) as exc:
            case.update(status="invalid", result=None, error=str(exc), auto_fit_eligible=False)
    if not curves:
        return
    start = max(c.start_mm - c.contact_mm for _, _, c in curves)
    end = min(c.end_mm - c.contact_mm for _, _, c in curves)
    for case, curve, _ in curves:
        try:
            result = {**analyze_curve(curve, config, fit_bounds=(start, end)),
                      **target_summary(run_path(case["attempts"][-1]), config)}
            result["warnings"].extend(cavity_warnings(run_path(case["attempts"][-1])))
            case.update(status="success", result=result, error="")
        except ValueError as exc:
            case.update(status="invalid", result=None, error=f"Automatischer gemeinsamer Fit: {exc}")


def rank_geometries(cases: list[dict], config: StudyConfig) -> list[dict]:
    stiffness_comparisons = stiffness_groups(cases, config)
    provisional = config.fit_mode == "automatic" and any(c["status"] in ("pending", "running") for c in cases)
    grouped = {}
    for case in cases:
        shell = case["parameters"]["measurement_shell"]
        key = (shell["radius_mm"], shell.get("thickness_x_mm", shell.get("thickness_mm")),
               shell.get("thickness_y_mm", shell.get("thickness_mm")))
        grouped.setdefault(key, []).append(case)
    rows = []
    for (radius, tx, ty), group in grouped.items():
        group.sort(key=lambda c: c["parameters"]["pressure_mmhg"])
        good = [c for c in group if c["status"] == "success" and c["result"] is not None]
        complete = len(good) == len(config.pressures)
        p = [c["parameters"]["pressure_mmhg"] for c in good]
        drives = [c["result"].get("achieved_drive_mm") for c in good]
        sensitive = complete and len(p) >= 2 and all(value is not None for value in drives)
        s = (drives[-1] - drives[0]) if sensitive else None
        partial = []
        monotonic = sensitive
        warnings = [w for c in good for w in c["result"]["warnings"]]
        relative = s
        status = ("Unvollständig / ungültig" if not complete else "Einzeldruck" if not sensitive
                  else "Nicht monoton / nicht positiv" if not monotonic else "Fit prüfen" if warnings else "Kandidat")
        if provisional:
            status = "Vorläufig · " + status
        rows.append({"radius_mm": radius, "thickness_mm": tx, "thickness_x_mm": tx, "thickness_y_mm": ty,
            "references": sorted({r for c in group for r in c["references"]}),
            "in_grid": any(c["in_grid"] for c in group), "sensitivity": s,
            "relative_change": relative, "interval_sensitivities": partial,
            "pressures": p, "end_drives": drives, "delta_end_drive_mm": s,
            "stiffnesses": [], "status": status, "complete": complete,
            "preferred": bool(monotonic and not warnings and not provisional), "provisional": provisional,
            "cases": [c["id"] for c in group],
            **{key: value for key, value in stiffness_comparisons[(radius, tx, ty)].items()
               if key in ('reference_pressure_mmhg', 'comparison_pressure_mmhg', 'reference_stiffness',
                          'comparison_stiffness', 'relative_stiffness_change_pct')},
            'stiffness_comparison_status': stiffness_comparisons[(radius, tx, ty)]['status'],
            'stiffness_comparison_notes': stiffness_comparisons[(radius, tx, ty)]['notes']})
    return sorted(rows, key=lambda r: (r["relative_change"] is None, -(r["relative_change"] or 0)))
