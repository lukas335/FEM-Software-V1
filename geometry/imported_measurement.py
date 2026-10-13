"""Exact editor curves -> conformal OCC volumes -> mechanical domain splitting."""
from __future__ import annotations

from dataclasses import dataclass
import math

from model.measurement_definition import boundary_counts, domain_name, reference_x, sketch_document


@dataclass
class ImportedMeasurement:
    document: object
    boundaries: dict
    symmetry: dict


def add_imported_measurement(gmsh, params, volumes, surfaces):
    from preprocessor_contours import endpoint_vertices, ordered_boundary
    document = sketch_document(params.measurement_definition)
    offset = reference_x(params.measurement_definition)
    vertices = endpoint_vertices(document)
    occ = gmsh.model.occ
    # Physical point IDs can differ under an explicit Coincident constraint.
    # Build one CAD junction, without changing the authored sketch snapshot.
    cad_points = {}
    sizes = []
    for ref in boundary_counts(document):
        edge = document.element(ref)
        if edge['kind'] == 'arc':
            sizes.append(min(edge['radius'], edge['radius'] * abs(math.radians(edge['sweep_angle']))))
        else:
            first = document.point(edge['start_point']); second = document.point(edge['end_point'])
            sizes.append(math.hypot(first['x'] - second['x'], first['y'] - second['y']))
    h = min(0.5, min(sizes) / 3)
    h *= {'coarse': 1.5, 'normal': 1, 'fine': 0.65}[params.mesh_quality]
    for point_id, vertex in vertices.items():
        if vertex not in cad_points:
            point = document.point(point_id)
            radial = 0.0 if abs(point['y']) <= 1e-6 else point['y']
            cad_points[vertex] = occ.addPoint(point['x'] - offset, radial, 0, h)
    curves = {}
    for ref in boundary_counts(document):
        edge = document.element(ref)
        start = document.point(edge['start_point']); end = document.point(edge['end_point'])
        a = cad_points[vertices[start['id']]]; b = cad_points[vertices[end['id']]]
        if edge['kind'] == 'line':
            curves[ref] = [occ.addLine(a, b)]
            continue
        center = document.point(edge['center_point'])
        c = occ.addPoint(center['x'] - offset, center['y'], 0)
        sweep = math.radians(edge['sweep_angle'])
        angle = math.atan2(start['y'] - center['y'], start['x'] - center['x'])
        steps = max(1, math.ceil(abs(sweep) / (math.pi / 2)))
        points = [a]
        for i in range(1, steps):
            t = angle + sweep * i / steps
            points.append(occ.addPoint(center['x'] - offset + edge['radius'] * math.cos(t),
                                       center['y'] + edge['radius'] * math.sin(t), 0, h))
        points.append(b)
        # OCC constructs the minor circular arc between endpoints. Subdivision
        # preserves both signs and avoids the <= pi limitation for circle arcs.
        curves[ref] = [occ.addCircleArc(first, c, second) for first, second in zip(points, points[1:])]
    original_volumes = []
    names = []
    for region in document.regions:
        wire = []
        for edge, forward in ordered_boundary(document, region['boundary']):
            segments = curves[edge['id']]
            wire.extend(segments if forward else [-tag for tag in reversed(segments)])
        section = occ.addPlaneSurface([occ.addCurveLoop(wire)])
        swept = occ.revolve([(2, section)], 0, 0, 0, 1, 0, 0, math.radians(params.wedge_angle_deg))
        tags = [(dim, tag) for dim, tag in swept if dim == 3]
        if len(tags) != 1:
            raise ValueError(f"{region['id']}: Rotation erzeugt keinen eindeutigen Volumenkörper.")
        original_volumes.extend(tags)
        names.append(domain_name(region['id']))
    if len(original_volumes) > 1:
        _, mapping = occ.fragment(original_volumes[:1], original_volumes[1:])
        for name, result in zip(names, mapping):
            volumes[name] = [tag for dim, tag in result if dim == 3]
    else:
        volumes[names[0]] = [original_volumes[0][1]]
    if any(len(volumes[name]) != 1 for name in names):
        raise ValueError('Überlappende oder fragmentierte Messschalenbereiche werden nicht unterstützt.')
    occ.synchronize()
    angle = math.radians(params.wedge_angle_deg)
    boundaries = {}
    symmetry = {}

    def samples(curve):
        lower, upper = gmsh.model.getParametrizationBounds(1, curve)
        return [gmsh.model.getValue(1, curve, [lower[0] + (upper[0] - lower[0]) * fraction])
                for fraction in (0, .25, .5, .75, 1)]

    for region in document.regions:
        name = domain_name(region['id'])
        boundaries[name] = {ref: [] for ref in region['boundary']}
        symmetry[name] = {0: [], 1: []}
        for _, face in gmsh.model.getBoundary([(3, volume) for volume in volumes[name]], oriented=False):
            face_curves = gmsh.model.getBoundary([(2, face)], oriented=False)
            curve_samples = [(curve, samples(curve)) for dim, curve in face_curves if dim == 1]
            coordinates = [point for _, points in curve_samples for point in points]
            if coordinates and all(abs(point[2]) < 1e-6 for point in coordinates):
                symmetry[name][0].append(face)
                continue
            if coordinates and all(abs(point[2] * math.cos(angle) - point[1] * math.sin(angle)) < 1e-6
                                   for point in coordinates):
                symmetry[name][1].append(face)
                continue
            matched = set()
            for _, points in curve_samples:
                if not all(abs(point[2]) < 1e-6 for point in points) or all(abs(point[1]) < 1e-6 for point in points):
                    continue
                for ref in region['boundary']:
                    edge = document.element(ref)
                    distances = []
                    for point in points:
                        actual = {'x': float(point[0]) + offset, 'y': float(point[1])}
                        projected = document._coincident_projection(actual, edge, False)
                        distances.append(math.hypot(actual['x'] - projected['x'], actual['y'] - projected['y']))
                    if max(distances) < 1e-5:
                        matched.add(ref)
            if len(matched) != 1:
                raise ValueError(f'{name}: Rotationsfläche {face} ist keiner eindeutigen Konturkante zuordenbar.')
            boundaries[name][matched.pop()].append(face)
        if not all(symmetry[name].values()):
            raise ValueError(f'{name}: Wedge-Symmetrieflächen fehlen.')
        for side, tags in symmetry[name].items():
            surfaces[f'{name}_symmetry_{side}'] = tags
        for ref, tags in boundaries[name].items():
            if tags:
                surfaces[f'{name}_boundary_{ref}'] = tags
            else:
                edge = document.element(ref)
                if edge['kind'] != 'line' or any(abs(document.point(edge[key])['y']) > 1e-6
                                               for key in ('start_point', 'end_point')):
                    raise ValueError(f'{name}/{ref}: Rotationsfläche fehlt.')
    return ImportedMeasurement(document, boundaries, symmetry)


def finish_imported_mesh(spec, nodes, elements, surfaces):
    """Duplicate shared contact nodes by bonded connected component, then tag faces."""
    document = spec.document
    domains = [domain_name(region['id']) for region in document.regions]
    parents = {name: name for name in domains}
    def root(name):
        while parents[name] != name:
            name = parents[name]
        return name
    for interface in document.interfaces:
        if interface['condition'] == 'bonded':
            parents[root(domain_name(interface['region_a']))] = root(domain_name(interface['region_b']))
    membership = {}
    for name in domains:
        for _, connectivity in elements[name]:
            for node in connectivity:
                membership.setdefault(node, set()).add(root(name))
    # OCC may retain sub-tolerance radial coordinates at arc/axis junctions.
    # Keep the rotational axis exact in the FEM mesh, not a tiny ring of nodes.
    for node in membership:
        x, y, z = nodes[node]
        if math.hypot(y, z) <= 1e-6:
            nodes[node] = (0.0 if abs(x) <= 1e-6 else x, 0.0, 0.0)
    copies = {}
    next_node = max(nodes) + 1
    for node, components in membership.items():
        for index, component in enumerate(sorted(components)):
            duplicate = node if index == 0 else next_node
            if index:
                nodes[duplicate] = nodes[node]
                next_node += 1
            copies[(node, component)] = duplicate
    for name in domains:
        mapping = {node: copies[(node, root(name))] for _, conn in elements[name] for node in conn}
        elements[name] = [(eid, tuple(mapping[node] for node in conn)) for eid, conn in elements[name]]
        for surface in list(surfaces):
            if surface.startswith(name + '_'):
                surfaces[surface] = [tuple(mapping[node] for node in face) for face in surfaces[surface]]
    for side in (0, 1):
        surfaces[f'measurement_symmetry_{side}'] = [face for name in domains for face in surfaces[f'{name}_symmetry_{side}']]
    counts = boundary_counts(document)
    support = []
    support_nodes = set()
    x_only_nodes = set()
    contact = []
    for region in document.regions:
        name = domain_name(region['id'])
        for ref in region['boundary']:
            faces = surfaces.get(f'{name}_boundary_{ref}', [])
            driven = region.get('boundary_motion', {}).get(ref) == 'follow_translation'
            guided = region.get('boundary_x_only', {}).get(ref, False)
            if driven or guided:
                edge_nodes = {node for face in faces for node in face}
                edge = document.element(ref)
                if edge['kind'] == 'line' and all(abs(document.point(edge[key])['y']) <= 1e-6
                                                 for key in ('start_point', 'end_point')):
                    offset = reference_x(document.data)
                    limits = sorted(document.point(edge[key])['x'] - offset for key in ('start_point', 'end_point'))
                    domain_nodes = {node for _, conn in elements[name] for node in conn}
                    edge_nodes.update(node for node in domain_nodes if math.hypot(nodes[node][1], nodes[node][2]) <= 1e-6
                                      and limits[0] - 1e-6 <= nodes[node][0] <= limits[1] + 1e-6)
                if driven:
                    support.extend(faces)
                    support_nodes.update(edge_nodes)
                if guided:
                    x_only_nodes.update(edge_nodes)
            if counts[ref] == 1:
                contact.extend(faces)
    if support:
        surfaces['measurement_support'] = list(dict.fromkeys(support))
    surfaces['measurement_contact'] = contact
    if not support_nodes or not contact:
        raise ValueError('Import benötigt Zustellknoten und äußere Kontaktflächen.')
    for interface in document.interfaces:
        for side, region_id, key in (('a', interface['region_a'], 'boundary_a'), ('b', interface['region_b'], 'boundary_b')):
            name = domain_name(region_id)
            surfaces[f'measurement_interface_{interface["id"]}_{side}'] = [face for ref in interface[key]
                    for face in surfaces.get(f'{name}_boundary_{ref}', [])]
        a = {node for face in surfaces[f'measurement_interface_{interface["id"]}_a'] for node in face}
        b = {node for face in surfaces[f'measurement_interface_{interface["id"]}_b'] for node in face}
        if not a or not b or (interface['condition'] == 'frictionless' and a & b) or (interface['condition'] == 'bonded' and a != b):
            raise ValueError(f"{interface['id']}: ungültige Interface-Knotentopologie.")
    return support_nodes, x_only_nodes
