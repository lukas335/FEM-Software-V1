"""Exact OCC measurement shell, sharing its geometry definition with the preview."""
from math import radians

from geometry.measurement_shell import measurement_geometry


def add_measurement_shell(gmsh, params, volumes_by_name, surfaces_by_name):
    shell = params.measurement_shell
    radius, tx, ty = shell.radius_mm, shell.thickness_x_mm, shell.thickness_y_mm
    geometry = measurement_geometry(radius, tx, ty)
    h = min(min(tx, ty) / 2, .75 if params.mesh_quality == "coarse" else .5)
    if params.mesh_quality == "fine":
        h = min(h, min(tx, ty) / 3)
    occ = gmsh.model.occ
    outer_center = occ.addPoint(geometry.outer_center, 0, 0)
    inner_center = (outer_center if tx == ty else occ.addPoint(geometry.inner_center, 0, 0))
    outer_tip = occ.addPoint(0, 0, 0, h)
    outer_rim = occ.addPoint(-radius, radius, 0, h)
    inner_rim = occ.addPoint(-radius, radius-ty, 0, h)
    inner_tip = occ.addPoint(-tx, 0, 0, h)
    curves = [occ.addCircleArc(outer_tip, outer_center, outer_rim),
              occ.addLine(outer_rim, inner_rim)]
    if geometry.tangent_extension:
        tangent = occ.addPoint(geometry.inner_center, geometry.inner_radius, 0, h)
        curves += [occ.addLine(inner_rim, tangent), occ.addCircleArc(tangent, inner_center, inner_tip)]
    else:
        curves.append(occ.addCircleArc(inner_rim, inner_center, inner_tip))
    curves.append(occ.addLine(inner_tip, outer_tip))
    section = occ.addPlaneSurface([occ.addCurveLoop(curves)])
    result = occ.revolve([(2, section)], 0, 0, 0, 1, 0, 0, radians(params.wedge_angle_deg))
    volumes = [tag for dim, tag in result if dim == 3]
    # Same OCC sweep ordering as the pressure shell: cap, volume, side faces.
    lateral = [tag for dim, tag in result[2:] if dim == 2]
    if len(volumes) != 1 or len(lateral) != (4 if geometry.tangent_extension else 3) or result[0][0] != 2:
        raise RuntimeError(f"Unexpected measurement shell boundary: {result}")
    volumes_by_name["measurement_shell"] = volumes
    surfaces_by_name["measurement_contact"] = [lateral[0]]
    surfaces_by_name["measurement_support"] = [lateral[1]]
    surfaces_by_name["measurement_inner_surface"] = lateral[2:]
    surfaces_by_name["measurement_symmetry_0"] = [section]
    surfaces_by_name["measurement_symmetry_1"] = [result[0][1]]
