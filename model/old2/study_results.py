"""Contact-relative, path-weighted regression and pressure sensitivity."""

from pathlib import Path
import json
import re
from xml.etree import ElementTree as ET
import numpy as np

from model.results import ForceCurve, automatic_fit_range, read_force_curve
from model.study import StudyConfig


def analyze_curve(curve: ForceCurve, config: StudyConfig, *, fit_bounds=None) -> dict:
    contact = automatic_fit_range(curve)
    x = np.asarray(curve.displacement_mm) - contact.contact_mm
    y = np.asarray(curve.force_n)
    if fit_bounds is not None:
        a, b = fit_bounds
    elif config.fit_mode == "automatic":
        a, b = contact.start_mm - contact.contact_mm, x[-1]
    else:
        a, b = config.fit_start_after_contact_mm, config.fit_end_after_contact_mm
    if b <= a:
        raise ValueError("Kein gemeinsamer belasteter Fitbereich vorhanden.")
    if x[0] > a or x[-1] < b:
        raise ValueError(f"Fitende δ={b:g} mm nicht erreicht (verfügbar: {x[-1]:.4f} mm). Hub erhöhen.")
    count = int(((x >= a) & (x <= b)).sum())
    if count < config.minimum_fit_points:
        raise ValueError(f"Nur {count} originale Fitpunkte; mindestens {config.minimum_fit_points} erforderlich.")
    # Exact integration of the piecewise-linear force curve: 2-point Gauss
    # quadrature per segment integrates all degree-2 least-squares terms exactly.
    knots = np.r_[a, x[(x > a) & (x < b)], b]
    half = np.diff(knots) / 2
    mid = (knots[1:] + knots[:-1]) / 2
    q = np.column_stack((mid - half / np.sqrt(3), mid + half / np.sqrt(3))).ravel()
    weights = np.repeat(half, 2)
    force = np.interp(q, x, y)
    mean_x, mean_y = np.average(q, weights=weights), np.average(force, weights=weights)
    k = np.sum(weights * (q - mean_x) * (force - mean_y)) / np.sum(weights * (q - mean_x)**2)
    intercept = mean_y - k * mean_x
    residual = np.sum(weights * (force - (k * q + intercept))**2)
    total = np.sum(weights * (force - mean_y)**2)
    r2 = float(1 - residual / total) if total > 1e-30 else None
    warnings = []
    if k <= 0:
        warnings.append("Keine positive Steifigkeit")
    if r2 is None or r2 < 0.98:
        warnings.append("Fit nicht annähernd linear (R² < 0,98)")
    return {"stiffness": float(k), "intercept": float(intercept), "r_squared": r2,
        "rmse_n": float(np.sqrt(residual / weights.sum())), "count": count,
        "contact_mm": contact.contact_mm, "contact_lower_mm": contact.lower_mm,
        "contact_upper_mm": contact.upper_mm, "fit_start_delta_mm": a, "fit_end_delta_mm": b,
        "max_force_n": float(y.max()), "warnings": warnings}


def check_preload(log_path: Path):
    """Require a contact-pressure record at every saved preload time."""
    root = ET.parse(log_path.with_name("model.feb")).getroot()
    if root.find("./Output/logfile/face_data[@name='contact_state']") is None:
        from solver.accepted_history import read_records, check_separated_preload
        check_separated_preload(log_path.with_name("model.feb"), read_records(log_path))
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
        k = [c["result"]["stiffness"] for c in good]
        p = [c["parameters"]["pressure_mmhg"] for c in good]
        sensitive = complete and len(p) >= 2
        s = (k[-1] - k[0]) / (p[-1] - p[0]) if sensitive else None
        partial = [(k[i+1] - k[i]) / (p[i+1] - p[i]) for i in range(len(k)-1)] if sensitive else []
        monotonic = sensitive and all(value > 0 for value in partial) and all(value > 0 for value in k)
        warnings = [w for c in good for w in c["result"]["warnings"]]
        relative = (k[-1] - k[0]) / k[0] if sensitive and k[0] > max(1e-8, 0.001 * max(map(abs, k))) else None
        status = ("Unvollständig / ungültig" if not complete else "Einzeldruck" if not sensitive
                  else "Nicht monoton / nicht positiv" if not monotonic else "Fit prüfen" if warnings else "Kandidat")
        if provisional:
            status = "Vorläufig · " + status
        rows.append({"radius_mm": radius, "thickness_x_mm": tx, "thickness_y_mm": ty,
            "references": sorted({r for c in group for r in c["references"]}),
            "in_grid": any(c["in_grid"] for c in group), "sensitivity": s,
            "relative_change": relative, "interval_sensitivities": partial,
            "pressures": p, "stiffnesses": k, "status": status, "complete": complete,
            "preferred": bool(monotonic and not warnings and not provisional), "provisional": provisional,
            "cases": [c["id"] for c in group]})
    return sorted(rows, key=lambda r: (r["sensitivity"] is None, -(r["sensitivity"] or 0)))
