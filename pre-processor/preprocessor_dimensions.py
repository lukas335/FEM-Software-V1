from __future__ import annotations

from preprocessor_common import *


class DimensionMixin:
        def add_dimension(self, kind, references, value, parameter=None, display_position=None, axis=None, reference_types=None):
            item = {"id": self._next_id("D"), "type": kind,
                    "references": list(references), "value": float(value),
                    "unit": "mm", "driving": True}
            if parameter: item["parameter"] = parameter
            if display_position: item["display_position"] = list(display_position)
            if axis in ("horizontal", "vertical"): item["axis"] = axis
            if reference_types: item["reference_types"] = list(reference_types)
            self.dimensions.append(item)
            return item["id"]

        def _arc_endpoint_elements(self, point_id):
            return [element for element in self.elements
                    if element.get("kind") == "arc"
                    and point_id in (element.get("start_point"), element.get("end_point"))]

        def _validate_after_dimension(self):
            # Derived region assignments must not lock editable sketch geometry.
            errors = self.validate(include_regions=False)
            violations = self._driving_dimension_violations()
            if errors:
                raise ValueError("Bemaßung erzeugt ungültige Geometrie: " + errors[0])
            if violations:
                raise ValueError("Bemaßung verletzt vorhandene Maße: " + ", ".join(violations))

        def set_dimension(self, dimension_id, value, validate=True):
            """Apply a driving dimension transactionally and solve connected geometry."""
            before = self.clone_data()
            try:
                dimension = self.dimension(dimension_id)
                value = float(value)
                if dimension is None or not math.isfinite(value) or value <= 0:
                    raise ValueError("Ungültige Bemaßung.")
                parameter = dimension.get("parameter")
                if parameter and validate:
                    self.set_parameter(parameter, value)
                    return
                dimension["value"] = value
                if parameter in self.data["parameters"]:
                    self.data["parameters"][parameter]["value"] = value
                if validate:
                    self.solve_constraints()
            except Exception:
                self.replace_data(before)
                raise
