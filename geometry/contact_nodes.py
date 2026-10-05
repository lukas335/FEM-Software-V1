"""Split an exactly matching contact mesh without changing any coordinates."""


def separate_pressure_contact(nodes, elements, surfaces):
    pressure_nodes = {n for _, conn in elements["pressure_shell"] for n in conn}
    cover_nodes = {n for part in ("cover_front", "cover_rear") for _, conn in elements[part] for n in conn}
    shared = pressure_nodes & cover_nodes
    contact_nodes = {n for tri in surfaces["pressure_contact"] for n in tri}
    pressure_faces = {tuple(sorted(tri)) for tri in surfaces["pressure_contact"]}
    cover_faces = {tuple(sorted(tri)) for tri in surfaces["cover_inner_surface"]}
    if not shared or shared != contact_nodes or pressure_faces != cover_faces:
        raise RuntimeError("Cannot split nonmatching pressure/cover contact")
    start = max(nodes) + 1
    copies = {node: start+i for i, node in enumerate(sorted(shared))}
    nodes.update({duplicate: nodes[node] for node, duplicate in copies.items()})
    elements["pressure_shell"] = [(eid, tuple(copies.get(n, n) for n in conn))
                                  for eid, conn in elements["pressure_shell"]]
    for name, triangles in surfaces.items():
        if name.startswith("pressure_"):
            surfaces[name] = [tuple(copies.get(n, n) for n in tri) for tri in triangles]
    # Cover-front/rear connectivity stays untouched: only the sliding interface splits.
