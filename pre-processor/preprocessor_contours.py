"""Derived contour topology; never changes authored points or constraints."""
from __future__ import annotations

import math

from preprocessor_common import GEOMETRY_TOLERANCE


def endpoint_vertices(document):
    """Connect coincident endpoint IDs within the geometric tolerance only."""
    vertices = {}
    representatives = []
    for element in document.elements:
        if element.get('construction'):
            continue
        for key in ('start_point', 'end_point'):
            point_id = element[key]
            if point_id in vertices:
                continue
            point = document.point(point_id)
            if point is None:
                raise ValueError(f'{element["id"]}: fehlender Punktbezug.')
            match = next((index for index, other in enumerate(representatives)
                          if math.hypot(point['x'] - other['x'], point['y'] - other['y'])
                          <= GEOMETRY_TOLERANCE), None)
            if match is None:
                match = len(representatives)
                representatives.append(point)
            vertices[point_id] = match
    return vertices


def ordered_boundary(document, boundary):
    """Return oriented, connected edges, or reject a stale/open boundary."""
    if len(boundary) < 2 or len(set(boundary)) != len(boundary):
        raise ValueError('Kontur ist nicht geschlossen.')
    vertices = endpoint_vertices(document)
    elements = []
    for element_id in boundary:
        element = document.element(element_id)
        if element is None or element.get('construction'):
            raise ValueError(f'Kontur verweist auf fehlende Randkante {element_id}.')
        elements.append(element)
    for first_forward in (True, False):
        ordered = []
        first = elements[0]
        initial = vertices[first['start_point' if first_forward else 'end_point']]
        current = initial
        for index, element in enumerate(elements):
            a = vertices[element['start_point']]
            b = vertices[element['end_point']]
            forward = first_forward if index == 0 else a == current
            if (a if forward else b) != current:
                break
            ordered.append((element, forward))
            current = b if forward else a
        else:
            if current == initial:
                return ordered
    raise ValueError('Bereichskontur hat eine Lücke oder eine falsche Kantenreihenfolge.')


def boundary_samples(document, boundary):
    samples = []
    for element, forward in ordered_boundary(document, boundary):
        start = document.point(element['start_point' if forward else 'end_point'])
        end = document.point(element['end_point' if forward else 'start_point'])
        points = [(start['x'], start['y'])]
        if element['kind'] == 'arc':
            center = document.point(element['center_point'])
            angle = math.atan2(start['y'] - center['y'], start['x'] - center['x'])
            sweep = math.radians(element['sweep_angle']) * (1 if forward else -1)
            steps = max(8, math.ceil(abs(math.degrees(sweep)) / 5))
            points.extend((center['x'] + element['radius'] * math.cos(angle + sweep * i / steps),
                           center['y'] + element['radius'] * math.sin(angle + sweep * i / steps))
                          for i in range(1, steps))
        points.append((end['x'], end['y']))
        samples.extend(points if not samples else points[1:])
    return samples


def bounded_faces(document):
    """Walk planar half-edges, returning faces rather than all graph cycles.

    Arc tangents order incident edges; signed circular-segment areas distinguish
    bounded faces from the exterior without a sampled-centroid heuristic.
    Intersections must already be split into actual contour junctions.
    """
    vertices = endpoint_vertices(document)
    outgoing = {}
    ends = {}
    angles = {}
    areas = {}
    for element in document.elements:
        if element.get('construction'):
            continue
        a = document.point(element['start_point'])
        b = document.point(element['end_point'])
        va, vb = vertices[a['id']], vertices[b['id']]
        if va == vb:
            continue
        cross = a['x'] * b['y'] - b['x'] * a['y']
        if element['kind'] == 'arc':
            center = document.point(element['center_point'])
            sweep = math.radians(element['sweep_angle'])
            area = (center['x'] * (b['y'] - a['y'])
                    - center['y'] * (b['x'] - a['x'])
                    + element['radius'] ** 2 * sweep) / 2
            sign = 1 if sweep > 0 else -1
            start_angle = math.atan2(a['y'] - center['y'], a['x'] - center['x']) + sign * math.pi / 2
            end_angle = math.atan2(b['y'] - center['y'], b['x'] - center['x']) - sign * math.pi / 2
            curvature = sign / element['radius']
        else:
            area = cross / 2
            start_angle = math.atan2(b['y'] - a['y'], b['x'] - a['x'])
            end_angle = start_angle + math.pi
            curvature = 0
        for forward, source, target, angle, bend, signed_area in (
                (True, va, vb, start_angle, curvature, area),
                (False, vb, va, end_angle, -curvature, -area)):
            edge = (element['id'], forward)
            outgoing.setdefault(source, []).append(edge)
            ends[edge] = target
            # Curvature resolves tangent rays without inventing a crossing.
            angles[edge] = (round(angle % (2 * math.pi), 10), bend)
            areas[edge] = signed_area
    for edges in outgoing.values():
        edges.sort(key=angles.__getitem__)
    successors = {}
    for edge, target in ends.items():
        reverse = (edge[0], not edge[1])
        edges = outgoing[target]
        successors[edge] = edges[(edges.index(reverse) - 1) % len(edges)]
    visited = set()
    faces = []
    for first in successors:
        if first in visited:
            continue
        walk = []
        edge = first
        while edge not in visited:
            visited.add(edge)
            walk.append(edge)
            edge = successors[edge]
        if edge != first or sum(areas[item] for item in walk) <= GEOMETRY_TOLERANCE ** 2:
            continue
        # A dangling edge is visited in both directions in the same face;
        # it is not part of the material contour.
        border = [item[0] for item in walk if (item[0], not item[1]) not in walk]
        if len(border) >= 2:
            ordered_boundary(document, border)
            faces.append(border)
    return faces
