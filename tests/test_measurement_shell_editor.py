import pytest

from measurement_shell_prototype import MeasurementShellSketch


def test_only_horizontal_and_vertical_lines_are_allowed():
    sketch = MeasurementShellSketch()
    start = sketch.add_point(0, 0)
    end = sketch.add_point(3, 0)
    sketch.add_line(start, end)
    bad = sketch.add_point(3, 2)
    with pytest.raises(ValueError):
        sketch.add_line(start, bad)


def test_lower_half_is_rejected_by_validation():
    sketch = MeasurementShellSketch()
    sketch.add_point(0, -1)
    assert any("unterhalb" in error for error in sketch.validate())


def test_trim_line_to_line_crossing():
    sketch = MeasurementShellSketch()
    a = sketch.add_point(0, 0); b = sketch.add_point(10, 0)
    c = sketch.add_point(5, 0); d = sketch.add_point(5, 5)
    horizontal = sketch.add_line(a, b)
    sketch.add_line(c, d)
    trimmed = sketch.trim(horizontal, 9, 0)
    line = sketch.element(horizontal)
    assert line["end_point"] == trimmed
    endpoint = sketch.point(trimmed)
    assert endpoint["x"] == pytest.approx(5)
    assert endpoint["y"] == pytest.approx(0)


def test_radius_dimension_moves_arc_endpoints():
    sketch = MeasurementShellSketch()
    center = sketch.add_point(0, 0); start = sketch.add_point(4, 0); end = sketch.add_point(0, 4)
    arc = sketch.add_arc(center, start, end, 4)
    dimension = sketch.add_dimension("radius", [arc], 4)
    sketch.set_dimension(dimension, 6)
    assert sketch.element(arc)["radius"] == pytest.approx(6)
    assert sketch.point(start)["x"] == pytest.approx(6)
    assert sketch.point(end)["y"] == pytest.approx(6)
