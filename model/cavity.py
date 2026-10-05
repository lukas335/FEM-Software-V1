"""Cavity history bound to its FEBio input; pressures are gauge pressures in mmHg.

volume_error is the relative change from the filled volume in phase 2. In
constant-pressure mode this change is allowed, not a volume-constraint error.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
import numpy as np


VOLUME_WARNING_REL = 1e-4  # 0.01%: retain the original quality target.
VOLUME_LIMIT_REL = 1e-3    # 0.1%: maximum accepted closed-cavity drift.


def assess_volume_error(max_error: float, *, constant_pressure: bool) -> list[str]:
    """Postprocessing acceptance only; does not weaken FEBio convergence."""
    if not np.isfinite(max_error) or max_error < 0:
        raise ValueError("Invalid cavity volume error")
    if constant_pressure:
        return []  # A reservoir permits volume change.
    if max_error > VOLUME_LIMIT_REL:
        raise ValueError(f"Sealed cavity volume error exceeds 0.1%: {max_error:.6g}")
    if max_error > VOLUME_WARNING_REL:
        return [f"Volumenabweichung {max_error:.4%}: über 0,01 %, innerhalb der 0,1-%-Grenze. Ergebnis prüfen."]
    return []


@dataclass(frozen=True)
class CavityHistory:
    time: np.ndarray
    pressure_mmhg: np.ndarray
    volume_mm3: np.ndarray
    volume_error: np.ndarray

    def at_time(self, time: float) -> tuple[float, float, float]:
        return tuple(float(np.interp(time, self.time, column))
                     for column in (self.pressure_mmhg, self.volume_mm3, self.volume_error))


def read_cavity(path: Path, input_hash: str) -> CavityHistory:
    with path.open(encoding="utf-8", newline="") as stream:
        if stream.readline().strip() != f"# model_sha256={input_hash}":
            raise ValueError("Druckdaten gehören nicht zu diesem Eingabenetz.")
        reader = csv.reader(stream)
        if next(reader, None) != ["time", "pressure_mmhg", "volume_full_mm3", "relative_volume_error"]:
            raise ValueError("Ungültiges Kavitätsdatenformat.")
        data = np.asarray([list(map(float, row)) for row in reader])
    if data.ndim != 2 or data.shape[1] != 4 or len(data) < 3 or not np.isfinite(data).all():
        raise ValueError("Unvollständige Druck-/Volumendaten.")
    if not (np.diff(data[:, 0]) > 0).all() or not (data[:, 2] > 0).all():
        raise ValueError("Ungültige Zeitordnung oder Kavitätsvolumina.")
    return CavityHistory(*data.T)
