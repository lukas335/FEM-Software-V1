"""Spherical outer surface of a variable-thickness cover; lengths in mm."""

from math import cos, isfinite, pi, radians, sin, sqrt


def cover_sphere(radius: float, tx: float, ty: float) -> tuple[float, float]:
    """Return (axial center offset, outer radius), cut at the inner equator.

    Inner sphere: center c, radius R. Outer pole: c-R-tx.
    Outer rim: x=c, radial coordinate R+ty. Either offset sign is allowed.
    """
    if not all(isfinite(v) and v > 0 for v in (radius, tx, ty)):
        raise ValueError("Radius, tx und ty müssen endlich und positiv sein.")
    a, b = radius + tx, radius + ty
    offset = (b - a) * (b + a) / (2 * a)
    return offset, a + offset


def cover_regions(radius: float, tx: float, ty: float, center_x: float,
                  free_cap_angle_deg: float) -> list[dict]:
    """Upper-section contours, with shared radial material edge and exact equator.

    Angles are measured from the front pole about the PRESSURE sphere center.
    The rear outer surface is concentric and has constant thickness ty.
    """
    if not isfinite(free_cap_angle_deg) or not 0 < free_cap_angle_deg < 90:
        raise ValueError("Freier Kappenwinkel muss zwischen 0° und 90° liegen.")
    offset, outer_radius = cover_sphere(radius, tx, ty)

    def point(theta, outer):
        rho = radius
        if outer:
            rho = (-offset * cos(theta) + sqrt(max(0, outer_radius**2 - offset**2 * sin(theta)**2))
                   if theta < pi / 2 else radius + ty)
        return center_x - rho * cos(theta), rho * sin(theta)

    split = radians(free_cap_angle_deg)
    regions = []
    for name, start, end in (("front", 0., split), ("rear", split, pi)):
        angles = sorted(set([start + (end - start) * i / 180 for i in range(181)]
                            + ([pi / 2] if start < pi / 2 < end else [])))
        inner = [point(t, False) for t in angles]
        outer = [point(t, True) for t in angles]
        regions.append(dict(name=name, inner=inner, outer=outer,
                            polygon=outer + inner[::-1]))
    return regions
