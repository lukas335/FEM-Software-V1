"""Read the actual FEBio input, not an inferred geometry preview."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

import numpy as np


@dataclass(frozen=True)
class InspectionGroup:
    kind: str
    name: str
    nodes: tuple[int, ...]
    faces: tuple[tuple[int, ...], ...]
    detail: str
    color: str


@dataclass(frozen=True)
class MeshInspection:
    groups: tuple[InspectionGroup, ...]
    warnings: tuple[str, ...]
    node_count: int
    element_count: int
    min_jacobian: float


def read_inspection(path: Path) -> MeshInspection:
    root = ET.parse(path).getroot()
    nodes = {int(node.get('id')): tuple(map(float, node.text.split(',')))
             for node in root.findall('./Mesh/Nodes/node')}
    sets = {entry.get('name'): tuple(map(int, entry.text.split(','))) if entry.text else ()
            for entry in root.findall('./Mesh/NodeSet')}
    surfaces = {entry.get('name'): tuple(tuple(map(int, face.text.split(','))) for face in entry)
                for entry in root.findall('./Mesh/Surface')}
    domains = {entry.get('name'): tuple(tuple(map(int, elem.text.split(','))) for elem in entry)
               for entry in root.findall('./Mesh/Elements')}
    owners = {name: {node for elem in elements for node in elem} for name, elements in domains.items()}
    groups, warnings = [], []
    axial_fixed = set()
    def group(kind, name, detail, color, *, node_set=None, surface=None, node_ids=()):
        faces = surfaces.get(surface, ())
        ids = sets.get(node_set, ()) if node_set else tuple(sorted({n for face in faces for n in face}))
        if node_ids:
            ids = tuple(sorted(node_ids))
        if (node_set and node_set not in sets) or (surface and surface not in surfaces):
            warnings.append(f'{name}: referenzierte Knotenmenge/Fläche fehlt.')
        groups.append(InspectionGroup(kind, name, ids, faces, detail, color))
        return set(ids)

    material_names = {entry.get('name'): entry.get('type') for entry in root.findall('./Material/material')}
    for domain in root.findall('./MeshDomains/SolidDomain'):
        name = domain.get('name')
        group('Material', name, f"{domain.get('mat')} · {material_names.get(domain.get('mat'), '?')}",
              '#64748b', node_ids=owners.get(name, set()))
    for bc in root.findall('./Boundary/bc'):
        axes = [axis for axis in ('x', 'y', 'z') if bc.findtext(axis + '_dof') == '1']
        detail = 'u' + '/u'.join(axes) + '=0 · alle Schritte'
        if bc.get('node_set') == 'measurement_x_only':
            detail = 'Bewegung nur entlang x: ' + detail + ' · ux separat frei/vorgegeben'
        ids = group('Fixierung', bc.get('node_set', '?'), detail,
                    '#334155', node_set=bc.get('node_set'))
        if 'x' in axes:
            axial_fixed.update(ids)
    for step in root.findall('./Step/step'):
        for bc in step.findall('./Boundary/bc'):
            dof, value = bc.findtext('dof'), bc.findtext('value')
            detail = f"{step.get('name')} (Schritt {step.get('id')}): u{dof}={value} mm"
            curve = bc.find('value')
            if curve is not None and curve.get('lc'):
                detail += f" × Lastkurve {curve.get('lc')}"
            ids = group('Zustellung', bc.get('node_set', '?'), detail, '#dc2626', node_set=bc.get('node_set'))
            if dof == 'x':
                axial_fixed.update(ids)
        for constraint in step.findall('./Constraints/constraint'):
            detail = f"{step.get('name')} (Schritt {step.get('id')}): {constraint.get('type')}"
            group('Volumen', constraint.get('surface', '?'), detail + ' · eingeschlossenes Volumen erhalten',
                  '#0284c7', surface=constraint.get('surface'))
    for constraint in root.findall('./Constraints/constraint'):
        group('Symmetrie', constraint.get('surface', '?'), constraint.get('type', '?'), '#94a3b8',
              surface=constraint.get('surface'))
    for load in root.findall('./Loads/surface_load'):
        pressure = load.find('pressure')
        curve_id = pressure.get('lc', '?') if pressure is not None else '?'
        group('Druck', load.get('surface', '?'), f"{load.get('type')}: {load.findtext('pressure')} MPa × Lastkurve {curve_id}",
              '#2563eb', surface=load.get('surface'))
    for curve in root.findall('./LoadData/load_controller'):
        values = '; '.join(point.text for point in curve.findall('./points/point'))
        group('Lastkurve', curve.get('id', '?'), 'Lastzeit/Faktor: ' + values + ' · Lastzeit ist keine physikalische Zeit', '#64748b')
    pairs = {pair.get('name'): (pair.findtext('primary'), pair.findtext('secondary'))
             for pair in root.findall('./Mesh/SurfacePair')}
    for contact in root.findall('./Contact/contact'):
        pair = contact.get('surface_pair')
        for side, surface in zip(('A', 'B'), pairs.get(pair, ())):
            group('Kontakt', f'{pair} · {side}', f"{contact.get('type')} · μ={contact.findtext('fric_coeff', '?')} · {surface}",
                  '#b45309' if side == 'A' else '#a855f7', surface=surface)
    tip = sets.get('measurement_inner_pole', ())
    if tip:
        group('Messpunkt', 'Wegauswertung', 'Zustellweg minus axiale Bewegung dieses Knotens', '#059669', node_set='measurement_inner_pole')

    # Sharing FEM nodes couples material domains mechanically; frictionless
    # contact is deliberately NOT treated as an axial anchor.
    measurement = [name for name in domains if name.startswith('measurement_')]
    remaining = set(measurement)
    while remaining:
        component = {remaining.pop()}
        component_nodes = set(owners[next(iter(component))])
        while True:
            connected = {name for name in remaining if owners[name] & component_nodes}
            if not connected:
                break
            component.update(connected); remaining.difference_update(connected)
            component_nodes.update(node for name in connected for node in owners[name])
        if not component_nodes & axial_fixed:
            warnings.append(' / '.join(sorted(component)) + ': keine axiale Verschiebungsvorgabe und keine '
                            'gemeinsamen Knoten mit axial gehaltenen Bereichen. Ein freier Starrkörper-Modus '
                            'ist möglich; reibungsfreier Kontakt garantiert keine axiale Lagerung.')
    tetrahedra = [elem for elements in domains.values() for elem in elements]
    xyz = np.asarray([[nodes[node] for node in elem] for elem in tetrahedra], dtype=float)
    if xyz.ndim != 3 or xyz.shape[1:] != (4, 3):
        raise ValueError('Die Netzprüfung unterstützt derzeit Tet4-Elemente.')
    jacobians = np.linalg.det(xyz[:, 1:] - xyz[:, :1])
    if not np.isfinite(xyz).all() or not np.isfinite(jacobians).all():
        raise ValueError('Nichtendliche Koordinaten/Jacobians im Eingabenetz.')
    if np.any(jacobians <= 0):
        warnings.append(f'{int(np.count_nonzero(jacobians <= 0))} Elemente mit nichtpositivem Ausgangs-Jacobian.')
    # Metadata adds bonded interfaces without inventing a contact condition.
    metadata_path = path.with_name('parameters.json')
    if metadata_path.is_file():
        data = json.loads(metadata_path.read_text(encoding='utf-8'))
        definition = data.get('measurement_definition') or {}
        for interface in definition.get('geometry', {}).get('interfaces', []):
            if interface['condition'] == 'bonded':
                group('Verklebt', interface['id'], 'Gemeinsame Netzknoten · kein gleitender Kontakt', '#0891b2',
                      surface=f"measurement_interface_{interface['id']}_a")
    return MeshInspection(tuple(groups), tuple(warnings), len(nodes), len(tetrahedra), float(jacobians.min()))


def solver_progress(directory: Path) -> str:
    """Bounded read of a live log; does not modify or control the solver."""
    candidates = [directory / 'solver.log', directory / 'stdout.txt']
    candidates += list((directory / 'trials').glob('*/stdout.txt'))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return 'Noch kein Solverprotokoll vorhanden. Die Netzprüfung startet keinen Solver.'
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    with latest.open('rb') as stream:
        stream.seek(max(0, latest.stat().st_size - 65536))
        tail = stream.read(65536).decode('utf-8', errors='replace')
    lines = [line.strip() for line in tail.splitlines() if re.search(
        r'Nonlinear solution status|time step|converg|displacement\s+\d|retries|reformation #|Max nr|TERMINATION|negative jacobian', line, re.I)]
    return str(latest.relative_to(directory)) + '\n' + '\n'.join(lines[-8:])
