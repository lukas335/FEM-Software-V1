"""Conformal CAD interfaces; pressure contact nodes are separated after meshing."""
from math import cos, sin, sqrt, radians, pi

from geometry.cover_shell import cover_sphere


def add_cover(gmsh, params, volumes, surfaces):
    occ = gmsh.model.occ
    cover = params.cover_shell
    radius = params.pressure_shell.radius_mm
    center = params.initial_gap_mm + radius
    offset, outer_radius = cover_sphere(radius, cover.thickness_x_mm, cover.thickness_y_mm)
    split = radians(cover.free_cap_angle_deg)
    wedge = radians(params.wedge_angle_deg)
    h = min(cover.thickness_x_mm, cover.thickness_y_mm) / (3 if params.mesh_quality == "fine" else 2)
    origin = occ.addPoint(center, 0, 0)
    eccentric = occ.addPoint(center + offset, 0, 0)
    angles = (0, split, pi / 2, pi)
    inner, outer = [], []
    for theta in angles:
        rho = (-offset * cos(theta) + sqrt(outer_radius**2 - offset**2 * sin(theta)**2)
               if theta <= pi / 2 else radius + cover.thickness_y_mm)
        radial_sine = 0.0 if theta in (0, pi) else sin(theta)
        inner.append(occ.addPoint(center - radius*cos(theta), radius*radial_sine, 0, h))
        outer.append(occ.addPoint(center - rho*cos(theta), rho*radial_sine, 0, h))
    ia = [occ.addCircleArc(inner[i], origin, inner[i+1]) for i in range(3)]
    oa = [occ.addCircleArc(outer[i], eccentric if i < 2 else origin, outer[i+1]) for i in range(3)]
    radial = {i: occ.addLine(inner[i], outer[i]) for i in (0, 1, 3)}
    loops = ([oa[0], -radial[1], -ia[0], radial[0]],
             [oa[1], oa[2], -radial[3], -ia[2], -ia[1], radial[1]])
    raw = []
    for curves in loops:
        section = occ.addPlaneSurface([occ.addCurveLoop(curves)])
        swept = occ.revolve([(2, section)], 0, 0, 0, 1, 0, 0, wedge)
        raw.append([(dim, tag) for dim, tag in swept if dim == 3])
        if len(raw[-1]) != 1:
            raise RuntimeError("Expected one volume per cover material")
    # Mesh the contact once, then duplicate its pressure-side nodes in the export.
    # Never leave the conformal pressure/cover interface mechanically bonded.
    pressure = [(3, tag) for tag in volumes["pressure_shell"]]
    if len(pressure) != 1:
        raise RuntimeError("Expected one pressure volume before fragmentation")
    _, mapping = occ.fragment(raw[0], raw[1] + pressure)
    if len(mapping) != 3:
        raise RuntimeError("Unexpected cover fragmentation")
    occ.synchronize()
    boundaries = {}
    for name, mapped in zip(("cover_front", "cover_rear", "pressure_shell"), mapping):
        tags = [tag for dim, tag in mapped if dim == 3]
        if not tags:
            raise RuntimeError(f"Missing {name} volume")
        volumes[name] = tags
        boundaries[name] = {abs(tag) for dim, tag in gmsh.model.getBoundary(
            [(3, tag) for tag in tags], combined=True, oriented=False) if dim == 2}
    interface = boundaries["cover_front"] & boundaries["cover_rear"]
    if not interface:
        raise RuntimeError("Cover material domains have no bonded interface")
    surfaces["cover_bond"] = sorted(interface)
    surfaces["cover_inner_surface"] = []
    surfaces["cover_outer_contact"] = []
    surfaces["cover_support"] = []
    for name in ("cover_front", "cover_rear"):
        boundary = boundaries[name]
        for side in (0, 1):
            surfaces[f"{name}_symmetry_{side}"] = []
        for face in sorted(boundary - interface):
            vertices = gmsh.model.getBoundary([(2, face)], combined=False, recursive=True, oriented=False)
            points = [gmsh.model.getValue(0, abs(tag), []) for dim, tag in vertices if dim == 0]
            if not points:
                raise RuntimeError("Cover face without vertices")
            if all(abs(p[2]) < 1e-7 for p in points):
                surfaces[f"{name}_symmetry_0"].append(face)
            elif all(abs(p[1]*sin(wedge)-p[2]*cos(wedge)) < 1e-7 for p in points):
                surfaces[f"{name}_symmetry_1"].append(face)
            elif all(abs(sqrt((p[0]-center)**2+p[1]**2+p[2]**2)-radius) < 1e-7 for p in points):
                surfaces["cover_inner_surface"].append(face)
            else:
                surfaces["cover_outer_contact"].append(face)
                if name == "cover_rear":
                    surfaces["cover_support"].append(face)
    for name, faces in surfaces.items():
        if name.startswith("cover") and not faces:
            raise RuntimeError(f"Missing cover surface: {name}")
    # Boolean operations may replace every pressure face tag: rebuild all groups.
    for name in list(surfaces):
        if name.startswith("pressure_"):
            del surfaces[name]
    contact = boundaries["pressure_shell"] & (boundaries["cover_front"] | boundaries["cover_rear"])
    if contact != set(surfaces["cover_inner_surface"]):
        raise RuntimeError("Pressure/cover interface is not fully conformal")
    surfaces["pressure_contact"] = sorted(contact)
    for name in ("pressure_inner_surface", "pressure_symmetry_0", "pressure_symmetry_1"):
        surfaces[name] = []
    for face in sorted(boundaries["pressure_shell"] - contact):
        vertices = gmsh.model.getBoundary([(2, face)], combined=False, recursive=True, oriented=False)
        points = [gmsh.model.getValue(0, abs(tag), []) for dim, tag in vertices if dim == 0]
        if not points:
            raise RuntimeError("Pressure face without vertices")
        if all(abs(p[2]) < 1e-7 for p in points):
            name = "pressure_symmetry_0"
        elif all(abs(p[1]*sin(wedge)-p[2]*cos(wedge)) < 1e-7 for p in points):
            name = "pressure_symmetry_1"
        else:
            name = "pressure_inner_surface"
        surfaces[name].append(face)
    if any(not faces for name, faces in surfaces.items() if name.startswith("pressure_")):
        raise RuntimeError("Incomplete pressure boundary after fragmentation")
