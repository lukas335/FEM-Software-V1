"""Validated curve data and least-squares analysis, independent of Qt/FEBio."""

import csv
from dataclasses import dataclass
from math import isfinite
from pathlib import Path


@dataclass(frozen=True)
class ForceCurve:
    time: tuple[float, ...]
    displacement_mm: tuple[float, ...]
    force_n: tuple[float, ...]
    source: Path


@dataclass(frozen=True)
class StiffnessFit:
    stiffness: float
    intercept: float
    r_squared: float | None
    compliance: float | None
    count: int


@dataclass(frozen=True)
class ContactFitRange:
    contact_mm: float
    lower_mm: float
    upper_mm: float
    start_mm: float
    end_mm: float
    threshold_n: float


def automatic_fit_range(curve: ForceCurve) -> ContactFitRange:
    """Estimate first contact from sustained positive force; never fit the free travel.

    Threshold: max(10 nN, 0.1% peak force), sustained for three saved states.
    A local zero-force extrapolation estimates contact within its sampled bracket.
    Regression uses only actual loaded samples, with a free intercept.
    """
    ordered = sorted(zip(curve.displacement_mm, curve.force_n))
    displacement = tuple(point[0] for point in ordered)
    force_values = tuple(point[1] for point in ordered)
    threshold = max(1e-8, 0.001 * max(force_values))
    if force_values[0] > threshold:
        raise ValueError("Kontakt bereits zu Beginn – Erstkontakt nicht bestimmbar. Spalt prüfen.")
    for index in range(1, len(force_values) - 2):
        if all(force > threshold for force in force_values[index:index + 3]):
            lo, hi = displacement[index - 1:index + 1]
            f1, f2 = force_values[index:index + 2]
            u2 = displacement[index + 1]
            estimate = hi - f1 * (u2 - hi) / (f2 - f1) if f2 > f1 else hi
            contact = max(lo, min(hi, estimate))
            return ContactFitRange(contact, lo, hi, hi, max(displacement), threshold)
    raise ValueError("Kein belastbarer Kontakt erkannt: mindestens drei belastete Punkte nötig.")


def read_force_curve(path: Path) -> ForceCurve:
    """Read the prototype CSV; forces are already scaled to the full rotation."""
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = ("time", "drive_mm", "reaction_full_N")
        if not set(columns).issubset(reader.fieldnames or []):
            raise ValueError("CSV benötigt die Spalten time, drive_mm und reaction_full_N.")
        for number, row in enumerate(reader, 2):
            try:
                point = tuple(float(row[name]) for name in columns)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Ungültige Zahlen in CSV-Zeile {number}.") from exc
            if not all(isfinite(value) for value in point):
                raise ValueError(f"Nicht endliche Zahlen in CSV-Zeile {number}.")
            rows.append(point)
    if len(rows) < 2:
        raise ValueError("Mindestens zwei Kurvenpunkte werden benötigt.")
    if any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("Die Zeitwerte müssen streng ansteigen.")
    return ForceCurve(tuple(p[0] for p in rows), tuple(p[1] for p in rows),
                      tuple(p[2] for p in rows), path.resolve())


def fit_stiffness(curve: ForceCurve, start: float, end: float) -> StiffnessFit:
    if not isfinite(start) or not isfinite(end) or start >= end:
        raise ValueError("Fit-Start muss kleiner als Fit-Ende sein.")
    if start < curve.displacement_mm[0] or end > curve.displacement_mm[-1]:
        raise ValueError("Fit-Bereich muss innerhalb der geladenen Kurve liegen.")
    points = [(u, f) for u, f in zip(curve.displacement_mm, curve.force_n) if start <= u <= end]
    if len(points) < 3:
        raise ValueError("Bitte mindestens drei Messpunkte für den Fit einschließen.")
    mean_u = sum(u for u, _ in points) / len(points)
    mean_f = sum(f for _, f in points) / len(points)
    variance_u = sum((u - mean_u) ** 2 for u, _ in points)
    if variance_u <= 0:
        raise ValueError("Der Fit benötigt unterschiedliche Verschiebungen.")
    k = sum((u - mean_u) * (f - mean_f) for u, f in points) / variance_u
    intercept = mean_f - k * mean_u
    residual = sum((f - k * u - intercept) ** 2 for u, f in points)
    total = sum((f - mean_f) ** 2 for _, f in points)
    return StiffnessFit(k, intercept, 1 - residual / total if total > 0 else None,
                        1 / k if k > 0 else None, len(points))
