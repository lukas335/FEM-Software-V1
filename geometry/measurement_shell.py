"""Exact eccentric inner sphere of the measurement hemisphere (mm)."""
from dataclasses import dataclass
from math import atan2, cos, sin, pi, isfinite


@dataclass(frozen=True)
class MeasurementGeometry:
    outer_center: float
    inner_center: float
    inner_radius: float
    inner_end_angle: float
    tangent_extension: bool = False


def measurement_geometry(radius, tx, ty):
    if not all(isfinite(v) for v in (radius, tx, ty)) or not (0 < tx < radius and 0 < ty < radius):
        raise ValueError("Messschale: 0 < tx, ty < R erforderlich.")
    a, b = radius - tx, radius - ty
    if ty > tx:
        return MeasurementGeometry(-radius, -radius+ty-tx, b, pi/2, True)
    offset = (a-b)*(a+b)/(2*a)
    inner_radius = (a*a+b*b)/(2*a)
    return MeasurementGeometry(-radius, -radius+offset, inner_radius, atan2(b, -offset))


def measurement_contour(radius, tx, ty, samples=181):
    geometry = measurement_geometry(radius, tx, ty)
    if samples < 2:
        raise ValueError("At least two contour points required")
    outer, inner = [], []
    for i in range(samples):
        theta = -pi/2 + pi*i/(samples-1)
        phi = -geometry.inner_end_angle + 2*geometry.inner_end_angle*i/(samples-1)
        outer.append((-radius+radius*cos(theta), radius*sin(theta)))
        inner.append((geometry.inner_center+geometry.inner_radius*cos(phi), geometry.inner_radius*sin(phi)))
    outer[0], outer[-1] = (-radius, -radius), (-radius, radius)
    if geometry.tangent_extension:
        inner = [(-radius, -(radius-ty))] + inner + [(-radius, radius-ty)]
    else:
        inner[0], inner[-1] = (-radius, -(radius-ty)), (-radius, radius-ty)
    return outer + inner[::-1]
