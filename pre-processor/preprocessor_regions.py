from __future__ import annotations

from preprocessor_common import *


class RegionMixin:
        def add_interface(self, region_a, region_b, boundary_a, boundary_b, condition="bonded"):
            if condition not in ("bonded", "frictionless"): raise ValueError("Unbekannte Interface-Bedingung.")
            self.interfaces.append({"id": self._next_id("I"), "region_a": region_a, "region_b": region_b,
                                    "boundary_a": list(boundary_a), "boundary_b": list(boundary_b), "condition": condition})

        def closed_loops(self):
            from preprocessor_contours import bounded_faces
            return bounded_faces(self)

        def region_validation_errors(self):
            from preprocessor_contours import ordered_boundary
            errors = []
            try:
                faces = {frozenset(face) for face in self.closed_loops()}
            except ValueError as error:
                errors.append(f'Bereichstopologie ungültig: {error}')
                faces = None
            seen = set()
            for region in self.regions:
                try:
                    ordered_boundary(self, region.get('boundary', []))
                    boundary = frozenset(region.get('boundary', []))
                    if boundary in seen:
                        raise ValueError('Bereichskontur ist mehrfach zugewiesen.')
                    seen.add(boundary)
                    if faces is not None and boundary not in faces:
                        raise ValueError('Kontur ist kein einzelner begrenzter Bereich.')
                except ValueError as error:
                    errors.append(f"Region {region.get('id')}: {error}")
            regions = {region.get('id'): region for region in self.regions}
            for interface in self.interfaces:
                first = regions.get(interface.get('region_a'))
                second = regions.get(interface.get('region_b'))
                if first is None or second is None:
                    errors.append(f"Interface {interface.get('id')}: fehlender Bereich.")
                    continue
                shared = set(first.get('boundary', [])).intersection(second.get('boundary', []))
                a, b = set(interface.get('boundary_a', [])), set(interface.get('boundary_b', []))
                if (not a or a != b or not a.issubset(shared)
                        or len(a) != len(interface.get('boundary_a', []))
                        or len(b) != len(interface.get('boundary_b', []))
                        or any(self.element(ref) is None for ref in a)):
                    errors.append(f"Interface {interface.get('id')}: ungültige gemeinsame Randkante.")
                if interface.get('condition') not in ('bonded', 'frictionless'):
                    errors.append(f"Interface {interface.get('id')}: unbekannte Bedingung.")
            for index, first in enumerate(self.regions):
                for second in self.regions[index + 1:]:
                    shared = set(first.get('boundary', [])).intersection(second.get('boundary', []))
                    pair = {first.get('id'), second.get('id')}
                    covered = set()
                    for interface in self.interfaces:
                        if {interface.get('region_a'), interface.get('region_b')} == pair:
                            covered.update(interface.get('boundary_a', []))
                    if shared - covered:
                        errors.append(f"Region {first.get('id')}/{second.get('id')}: Interface fehlt für gemeinsame Randkante.")
            return errors

        def remap_split_boundary(self, original_id, new_id):
            """Splitting preserves explicitly assigned edge behavior on both pieces."""
            original = self.element(original_id)
            start = self.point(original['start_point'])
            for region in self.regions:
                boundary = region.get('boundary', [])
                if original_id not in boundary:
                    continue
                index = boundary.index(original_id)
                neighbor = self.element(boundary[(index + 1) % len(boundary)])
                reversed_order = neighbor is not None and any(
                    math.hypot(self.point(neighbor[key])['x'] - start['x'],
                               self.point(neighbor[key])['y'] - start['y']) <= GEOMETRY_TOLERANCE
                    for key in ('start_point', 'end_point'))
                boundary[index:index + 1] = [new_id, original_id] if reversed_order else [original_id, new_id]
                motion = region.setdefault('boundary_motion', {})
                motion[new_id] = motion.get(original_id, 'free_contact')
                if original_id in region.get('boundary_x_only', {}):
                    region['boundary_x_only'][new_id] = region['boundary_x_only'][original_id]
            for interface in self.interfaces:
                for key in ('boundary_a', 'boundary_b'):
                    boundary = list(interface.get(key, []))
                    interface[key] = boundary
                    if original_id in boundary:
                        index = boundary.index(original_id)
                        boundary[index:index + 1] = [original_id, new_id]

        def ensure_regions_current(self):
            """Rebuild obsolete stored contours; do not invent missing geometry."""
            if not self.regions and not self.interfaces:
                return False
            loops = self.closed_loops()
            expected = {frozenset(loop) for loop in loops}
            stored = {frozenset(region.get('boundary', [])) for region in self.regions}
            if expected == stored and not self.region_validation_errors():
                return False
            self.detect_regions()
            return True

        def detect_regions(self):
            """Derive bounded material faces and migrate surviving assignments."""
            geometry_errors = self.validate(include_regions=False)
            if geometry_errors:
                raise ValueError("Geometrie ist vor der Bereichserkennung ungültig: " + " | ".join(geometry_errors[:4]))
            loops = self.closed_loops()
            previous = list(self.regions)
            matches = {}
            used = set()
            # Exact matches first. Otherwise migrate only an unambiguous majority
            # of surviving old edges, never arbitrary material/contact settings.
            candidates = []
            active = {element['id'] for element in self.elements}
            for index, boundary in enumerate(loops):
                current = set(boundary)
                for old_index, old in enumerate(previous):
                    old_boundary = set(old.get('boundary', []))
                    surviving = old_boundary.intersection(active)
                    common = len(current.intersection(surviving))
                    exact = current == old_boundary
                    score = common / max(len(surviving), 1)
                    if exact or (common >= 2 and score > 0.5):
                        candidates.append((exact, score, common, index, old_index))
            for exact, score, common, index, old_index in sorted(candidates, reverse=True):
                if index in matches or old_index in used:
                    continue
                rivals = [item for item in candidates if item[3] == index and item[4] != old_index
                          and item[4] not in used and (not exact or item[0])]
                rivals += [item for item in candidates if item[4] == old_index and item[3] != index
                           and item[3] not in matches and (not exact or item[0])]
                if rivals:
                    continue
                matches[index] = previous[old_index]
                used.add(old_index)
            reserved = {region['id'] for region in previous}
            regions = []
            for index, boundary in enumerate(loops):
                old = matches.get(index, {})
                if old:
                    region_id = old['id']
                else:
                    number = 1
                    while f'R{number:03d}' in reserved:
                        number += 1
                    region_id = f'R{number:03d}'
                    reserved.add(region_id)
                motion = old.get('boundary_motion', {})
                regions.append({
                    'id': region_id,
                    'name': old.get('name', f'Bereich {index + 1}'),
                    'material': old.get('material', next(iter(MATERIALS), '')),
                    'boundary': list(boundary),
                    'boundary_motion': {ref: motion.get(ref, 'free_contact') for ref in boundary},
                    'connected_to': copy.deepcopy(old.get('connected_to', [])),
                })
                if 'boundary_x_only' in old:
                    regions[-1]['boundary_x_only'] = {ref: enabled for ref, enabled
                            in old['boundary_x_only'].items() if ref in boundary}
            old_interfaces = {frozenset((item.get('region_a'), item.get('region_b'))): item
                              for item in self.interfaces}
            reserved_interfaces = {item['id'] for item in self.interfaces}
            interfaces = []
            for first_index, first in enumerate(regions):
                for second in regions[first_index + 1:]:
                    shared = sorted(set(first['boundary']).intersection(second['boundary']))
                    if not shared:
                        continue
                    old = old_interfaces.get(frozenset((first['id'], second['id'])), {})
                    if old:
                        interface_id = old['id']
                    else:
                        number = 1
                        while f'I{number:03d}' in reserved_interfaces:
                            number += 1
                        interface_id = f'I{number:03d}'
                        reserved_interfaces.add(interface_id)
                    interfaces.append({
                        'id': interface_id, 'region_a': first['id'], 'region_b': second['id'],
                        'boundary_a': list(shared), 'boundary_b': list(shared),
                        'condition': old.get('condition', 'bonded'),
                    })
            self.regions[:] = regions
            self.interfaces[:] = interfaces
            return len(regions)

        def set_boundary_motion(self, region_id, element_id, behavior):
            if behavior not in ("follow_translation", "free_contact"):
                raise ValueError("Unbekanntes Randverhalten.")
            region = next((item for item in self.regions if item.get("id") == region_id), None)
            if region is None or element_id not in region.get("boundary", []):
                raise ValueError("Randkante nicht gefunden.")
            region.setdefault("boundary_motion", {})[element_id] = behavior

        def simulation_boundary_conditions(self):
            if errors := self.validate():
                raise ValueError('Ungültige Geometrie/Bereichszuweisung: ' + ' | '.join(errors[:4]))
            return [{"region_id": region.get("id"), "element_id": element_id,
                     "behavior": region.get("boundary_motion", {}).get(element_id, "free_contact"),
                     "x_only": region.get('boundary_x_only', {}).get(element_id, False)}
                    for region in self.regions for element_id in region.get("boundary", [])]

        def set_boundary_x_only(self, region_id, element_id, enabled):
            if not isinstance(enabled, bool):
                raise ValueError('X-Führung muss ein boolescher Wert sein.')
            region = next((item for item in self.regions if item.get('id') == region_id), None)
            if region is None or element_id not in region.get('boundary', []):
                raise ValueError('Randkante nicht gefunden.')
            if enabled:
                region.setdefault('boundary_x_only', {})[element_id] = True
            elif 'boundary_x_only' in region:
                region['boundary_x_only'].pop(element_id, None)

        def study_variants(self):
            variables = [(n, p) for n, p in self.data["parameters"].items() if p.get("study_variable")]
            choices = []
            for name, p in variables:
                values = p.get("values")
                if values is None and p.get("minimum") is not None and p.get("maximum") is not None:
                    values = [p["minimum"], p["value"], p["maximum"]]
                choices.append([(name, float(v)) for v in (values or [p["value"]])])
            for combo in itertools.product(*choices) if choices else [()]:
                candidate = type(self)(self.clone_data())
                try:
                    if combo:
                        candidate.set_parameters(dict(combo))
                    errors = candidate.validate()
                except ValueError as error:
                    errors = [str(error)]
                yield dict(combo), candidate, errors

        def to_simulation_parameters(self, pressure_radius_mm=20.0,
                                     pressure_thickness_mm=1.0,
                                     pressure_material="silicone_A30"):
            """Compatibility bridge for the current concentric-shell API."""
            if errors := self.validate():
                raise ValueError('Ungültige Geometrie/Bereichszuweisung: ' + ' | '.join(errors[:4]))
            from model.parameters import MeasurementShellParameters, ShellParameters, SimulationParameters
            p = self.data["parameters"]
            radius = float(p.get("outer_radius", {}).get("value", 10.0))
            thickness = float(p.get("wall_thickness", {}).get("value", 1.0))
            material = self.regions[0].get("material", "silicone_A30") if self.regions else "silicone_A30"
            result = SimulationParameters(MeasurementShellParameters(radius, thickness, thickness, material),
                                          ShellParameters(pressure_radius_mm, pressure_thickness_mm, pressure_material))
            if errors := result.validate(): raise ValueError(" ".join(errors))
            return result
