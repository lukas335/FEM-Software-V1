"""Public Qt-free sketch document assembled from focused mixins."""
from __future__ import annotations

from preprocessor_core import SketchCoreMixin
from preprocessor_dimensions import DimensionMixin
from preprocessor_geometry import GeometryMixin
from preprocessor_regions import RegionMixin
from preprocessor_relations import RelationMixin
from preprocessor_solver import SolverMixin


class MeasurementShellSketch(SketchCoreMixin, SolverMixin, RelationMixin, DimensionMixin, GeometryMixin, RegionMixin):
    """JSON-compatible sketch document; Qt is intentionally not used here."""
    pass
