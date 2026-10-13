"""Shared, Qt-free values for the measurement-shell pre-processor."""
from __future__ import annotations

import copy
import itertools
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from model.materials import MATERIALS

FORMAT_VERSION = 2
GEOMETRY_TOLERANCE = 1e-6


def default_arc_sweep(center, start, end):
    """Choose the upper-half-shell arc direction whenever possible."""
    a0 = math.degrees(math.atan2(start["y"] - center["y"], start["x"] - center["x"]))
    a1 = math.degrees(math.atan2(end["y"] - center["y"], end["x"] - center["x"]))
    ccw = (a1 - a0) % 360
    if ccw <= 1e-9:
        ccw = 360.0
    candidates = (ccw, ccw - 360.0)

    def minimum_y(sweep):
        values = []
        for index in range(25):
            angle = math.radians(a0 + sweep * index / 24.0)
            values.append(center["y"] + math.hypot(start["x"] - center["x"], start["y"] - center["y"]) * math.sin(angle))
        return min(values)

    upper = [sweep for sweep in candidates if minimum_y(sweep) >= -1e-7]
    if upper:
        return max(upper, key=lambda sweep: minimum_y(sweep))
    return min(candidates, key=abs)


@dataclass
class Parameter:
    value: float
    unit: str = "mm"
    type: str = "float"
    study_variable: bool = False
    values: list[float] | None = None
    minimum: float | None = None
    maximum: float | None = None
