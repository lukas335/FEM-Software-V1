from __future__ import annotations

from preprocessor_model import MeasurementShellSketch

def demo_model():
    s = MeasurementShellSketch()
    s.add_parameter("outer_radius", 8.0, study_variable=True, values=[7.0, 8.0, 9.0])
    s.add_parameter("inner_radius", 6.75)
    s.add_parameter("wall_thickness", 1.25)
    # Actual measurement-shell-like start contour: a right-facing upper
    # semicircular shell with two straight end sections on the rotation axis.
    center = s.add_point(0, 0.0)
    outer_bottom = s.add_point(8.0, 0.0); outer_top = s.add_point(0, 8.0)
    inner_top = s.add_point(0, 6.75); inner_bottom = s.add_point(6.75, 0.0)
    s.add_arc(center, outer_bottom, outer_top, 8.0, "A001", sweep_angle=90.0)
    s.add_line(outer_top, inner_top, "L001")
    s.add_arc(center, inner_top, inner_bottom, 6.75, "A002", sweep_angle=-90.0)
    s.add_line(inner_bottom, outer_bottom, "L002")
    s.regions.append({"id": "region_001", "name": "Messschale", "material": "silicone_A30",
                      "boundary": ["A001", "L001", "A002", "L002"], "connected_to": []})
    return s
