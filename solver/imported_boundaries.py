"""Mesh-based normals for arbitrary imported material/contact boundaries."""
from itertools import combinations

from model.measurement_definition import domain_name


def imported_surface_orienter(mesh, definition):
    owners = {}
    for domain, elements in mesh.elements.items():
        if not domain.startswith('measurement_'):
            continue
        for _, connectivity in elements:
            for face in combinations(connectivity, 3):
                fourth = next(node for node in connectivity if node not in face)
                owners.setdefault(frozenset(face), []).append((domain, fourth))
    surface_domains = {}
    for region in definition['geometry']['regions']:
        domain = domain_name(region['id'])
        for name in mesh.surfaces:
            if name.startswith(domain + '_'):
                surface_domains[name] = domain
    for interface in definition['geometry']['interfaces']:
        surface_domains[f'measurement_interface_{interface["id"]}_a'] = domain_name(interface['region_a'])
        surface_domains[f'measurement_interface_{interface["id"]}_b'] = domain_name(interface['region_b'])

    def orient(face, name):
        candidates = owners.get(frozenset(face), [])
        domain = surface_domains.get(name)
        if domain:
            candidates = [entry for entry in candidates if entry[0] == domain]
        if len(candidates) != 1:
            raise ValueError(f'{name}: Flächennormale nicht eindeutig einem Volumenelement zuordenbar.')
        a, b, c = [mesh.nodes[node] for node in face]
        ab = [b[i] - a[i] for i in range(3)]
        ac = [c[i] - a[i] for i in range(3)]
        normal = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
        fourth = mesh.nodes[candidates[0][1]]
        if sum(normal[i] * (fourth[i] - a[i]) for i in range(3)) > 0:
            return face[0], face[2], face[1]
        return face
    return orient
