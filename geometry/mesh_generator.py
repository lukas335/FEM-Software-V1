"""Coarse tetrahedral 3D wedge for the isolated FEBio baseline.

Coordinates use x as the symmetry axis. The measurement hemisphere is open;
the pressure body is a complete hollow sphere with its rear half fixed.
"""

from dataclasses import dataclass
from math import radians

from model.parameters import SimulationParameters
from geometry.measurement_mesh import add_measurement_shell


@dataclass(frozen=True)
class WedgeMesh:
    nodes: dict[int, tuple[float, float, float]]
    elements: dict[str, list[tuple[int, tuple[int, ...]]]]
    surfaces: dict[str, list[tuple[int, ...]]]
    node_sets: dict[str, list[int]]


def generate_wedge(params: SimulationParameters) -> WedgeMesh:
    """Make a first-order tet mesh. The gap allows pressure inflation before contact."""
    errors = params.validate()
    if errors:
        raise ValueError("; ".join(errors))
    try:
        import gmsh
    except ImportError as exc:
        raise RuntimeError("Gmsh Python package missing: pip install gmsh") from exc

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("hemisphere_and_closed_sphere")
        angle = radians(params.wedge_angle_deg)
        surfaces_by_name: dict[str, list[int]] = {}
        volumes_by_name: dict[str, list[int]] = {}

        def shell(prefix: str, radius: float, thickness: float, center: float, direction: int,
                  full: bool = False) -> None:
            inner = radius - thickness
            h = min(thickness / 2, 0.75 if params.mesh_quality == "coarse" else 0.5)
            if params.mesh_quality == "fine":
                h = min(h, thickness / 3)
            occ = gmsh.model.occ
            center_pt = occ.addPoint(center, 0, 0)
            outer_tip = occ.addPoint(center + direction * radius, 0, 0, h)
            outer_base = occ.addPoint(center, radius, 0, max(h, 1.0))
            inner_base = occ.addPoint(center, inner, 0, max(h, 1.0))
            inner_tip = occ.addPoint(center + direction * inner, 0, 0, h)
            outer_arc = occ.addCircleArc(outer_tip, center_pt, outer_base)
            inner_arc = occ.addCircleArc(inner_base, center_pt, inner_tip)
            pole = occ.addLine(inner_tip, outer_tip)
            if full:
                outer_back = occ.addPoint(center - direction * radius, 0, 0, h)
                inner_back = occ.addPoint(center - direction * inner, 0, 0, h)
                back_outer_arc = occ.addCircleArc(outer_base, center_pt, outer_back)
                back_pole = occ.addLine(outer_back, inner_back)
                back_inner_arc = occ.addCircleArc(inner_back, center_pt, inner_base)
                wire = occ.addCurveLoop([outer_arc, back_outer_arc, back_pole, back_inner_arc, inner_arc, pole])
            else:
                base = occ.addLine(outer_base, inner_base)
                wire = occ.addCurveLoop([outer_arc, base, inner_arc, pole])
            section = occ.addPlaneSurface([wire])
            result = occ.revolve([(2, section)], 0, 0, 0, 1, 0, 0, angle)
            volume = [tag for dim, tag in result if dim == 3]
            if len(volume) != 1:
                raise RuntimeError(f"Expected one {prefix} volume, got {result}")
            volumes_by_name[prefix + "_shell"] = volume
            # OCC returns the rotated cap, volume, then the swept surfaces
            # in the input wire's curve order. The axis edge has no area.
            lateral = [tag for dim, tag in result[2:] if dim == 2]
            if len(lateral) != (4 if full else 3):
                raise RuntimeError(f"Incomplete {prefix} swept boundary: {result}")
            surfaces_by_name[prefix + "_contact"] = [lateral[0]]
            surfaces_by_name[prefix + "_support"] = [lateral[1]]
            surfaces_by_name[prefix + "_inner_surface"] = lateral[2:] if full else [lateral[2]]
            surfaces_by_name[prefix + "_symmetry_0"] = [section]
            # The first surface in OCC's revolve result is the rotated cap.
            if result[0][0] != 2:
                raise RuntimeError("Unexpected Gmsh revolve order")
            surfaces_by_name[prefix + "_symmetry_1"] = [result[0][1]]

        imported = None
        if params.measurement_definition is None:
            add_measurement_shell(gmsh, params, volumes_by_name, surfaces_by_name)
        else:
            from geometry.imported_measurement import add_imported_measurement
            imported = add_imported_measurement(gmsh, params, volumes_by_name, surfaces_by_name)
        shell("pressure", params.pressure_shell.radius_mm, params.pressure_shell.thickness_mm,
              params.initial_gap_mm + params.pressure_shell.radius_mm, -1, full=True)
        if params.cover_shell.enabled:
            from geometry.cover_mesh import add_cover
            surfaces_by_name["pressure_contact"] += surfaces_by_name["pressure_support"]
            del surfaces_by_name["pressure_support"]
            add_cover(gmsh, params, volumes_by_name, surfaces_by_name)
        gmsh.model.occ.synchronize()
        for name, tags in volumes_by_name.items():
            group = gmsh.model.addPhysicalGroup(3, tags)
            gmsh.model.setPhysicalName(3, group, name)
        for name, tags in surfaces_by_name.items():
            group = gmsh.model.addPhysicalGroup(2, tags)
            gmsh.model.setPhysicalName(2, group, name)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        thicknesses = [params.measurement_shell.thickness_x_mm,
                      params.measurement_shell.thickness_y_mm, params.pressure_shell.thickness_mm]
        if params.cover_shell.enabled:
            thicknesses += [params.cover_shell.thickness_x_mm, params.cover_shell.thickness_y_mm]
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", min(thicknesses) / (3 if params.mesh_quality == "fine" else 2))
        maximum = 2.0 if params.mesh_quality == "coarse" else 1.0
        if params.cover_shell.enabled:
            from geometry.cover_mesh_sizing import configure_cover_mesh_size
            maximum = configure_cover_mesh_size(gmsh, params, volumes_by_name)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", maximum)
        gmsh.model.mesh.generate(3)
        node_tags, coords, _ = gmsh.model.mesh.getNodes()
        nodes = {int(tag): tuple(float(v) for v in coords[3*i:3*i+3]) for i, tag in enumerate(node_tags)}
        elements: dict[str, list[tuple[int, tuple[int, ...]]]] = {}
        for name, volume_tags in volumes_by_name.items():
            records = []
            for vol in volume_tags:
                types, ids, blocks = gmsh.model.mesh.getElements(3, vol)
                for element_type, element_ids, block in zip(types, ids, blocks):
                    if element_type != 4:
                        raise RuntimeError(f"Only tet4 supported, got Gmsh type {element_type}")
                    records.extend((int(eid), tuple(int(v) for v in block[4*i:4*i+4])) for i, eid in enumerate(element_ids))
            elements[name] = records
        # Circle-center CAD points are not part of either shell volume.
        used_nodes = {node for records in elements.values() for _, element in records for node in element}
        nodes = {node: xyz for node, xyz in nodes.items() if node in used_nodes}
        facets: dict[str, list[tuple[int, ...]]] = {}
        for name, faces in surfaces_by_name.items():
            triangles = []
            for face in faces:
                types, _, blocks = gmsh.model.mesh.getElements(2, face)
                for element_type, block in zip(types, blocks):
                    if element_type != 2:
                        raise RuntimeError(f"Only tri3 supported, got Gmsh type {element_type}")
                    triangles.extend(tuple(int(v) for v in block[3*i:3*i+3]) for i in range(len(block) // 3))
            if not triangles:
                raise RuntimeError(f"Surface {name} has no triangles")
            facets[name] = triangles
        if params.cover_shell.enabled:
            from geometry.contact_nodes import separate_pressure_contact
            separate_pressure_contact(nodes, elements, facets)
        imported_support = None
        imported_x_only = set()
        if imported is not None:
            from geometry.imported_measurement import finish_imported_mesh
            imported_support, imported_x_only = finish_imported_mesh(imported, nodes, elements, facets)
        support_name = "cover_support" if params.cover_shell.enabled else "pressure_support"
        node_sets = {support_name: sorted({node for facet in facets[support_name] for node in facet})}
        node_sets['measurement_support'] = (sorted(imported_support) if imported_support is not None
                else sorted({node for facet in facets['measurement_support'] for node in facet}))
        if imported_x_only:
            node_sets['measurement_x_only'] = sorted(imported_x_only)
        measurement_nodes = {node for name, records in elements.items() if name.startswith('measurement_')
                             for _, element in records for node in element}
        if imported is not None:
            from model.measurement_definition import domain_name, reference_region
            reference_domain = domain_name(reference_region(params.measurement_definition))
            measurement_nodes = {node for _, element in elements[reference_domain] for node in element}
        pole_target = (-params.measurement_shell.thickness_x_mm, 0.0, 0.0)
        if imported is not None:
            from model.measurement_definition import reference_x
            point_id = params.measurement_definition['metadata']['sensor_tip_point']
            point = imported.document.point(point_id)
            pole_target = (point['x'] - reference_x(params.measurement_definition),
                           0.0 if abs(point['y']) <= 1e-6 else point['y'], 0.0)
        pole = min(measurement_nodes, key=lambda node: sum((nodes[node][i] - pole_target[i]) ** 2 for i in range(3)))
        if sum((nodes[pole][i] - pole_target[i]) ** 2 for i in range(3)) > 1e-12:
            raise RuntimeError("Messreferenz-Knoten im gewählten Bereich fehlt" if imported is not None
                               else "Innerer Messschalen-Polknoten bei r=0 fehlt")
        node_sets["measurement_inner_pole"] = [pole]
        pressure_nodes = {node for _, element in elements["pressure_shell"] for node in element}
        center = params.initial_gap_mm + params.pressure_shell.radius_mm
        if params.cover_shell.enabled:
            front = {n for _, conn in elements["cover_front"] for n in conn}
            rear = {n for _, conn in elements["cover_rear"] for n in conn}
            bonded = {n for face in facets["cover_bond"] for n in face}
            if not bonded or not bonded <= (front & rear):
                raise RuntimeError("Cover bond must have shared nodes")
            if pressure_nodes & (front | rear):
                raise RuntimeError("Pressure/cover contact must NOT share nodes")
        else:
            node_sets["pressure_support"] = sorted(node for node in pressure_nodes if nodes[node][0] >= center - 1e-8)
        if any(not group for group in elements.values()):
            raise RuntimeError("A shell volume has no tetrahedra")
        return WedgeMesh(nodes, elements, facets, node_sets)
    finally:
        gmsh.finalize()
