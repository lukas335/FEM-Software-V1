"""Validated editor snapshots and simulation-coordinate mapping (Qt-free)."""
from __future__ import annotations

import copy
from collections import Counter
import json
from pathlib import Path
import re
import sys


def sketch_document(data):
    directory = str(Path(__file__).resolve().parents[1] / 'pre-processor')
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from preprocessor_model import MeasurementShellSketch
    return MeasurementShellSketch(data)


def domain_name(region_id):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', str(region_id)):
        raise ValueError('Bereichs-ID darf nur Buchstaben, Ziffern und Unterstriche enthalten.')
    return f'measurement_{region_id}'


def boundary_counts(document):
    return Counter(ref for region in document.regions for ref in region['boundary'])


def axis_references(document):
    active = {document.element(ref)[key] for ref, count in boundary_counts(document).items()
              if count == 1 for key in ('start_point', 'end_point')}
    return sorted((document.point(ref) for ref in active if abs(document.point(ref)['y']) <= 1e-6),
                  key=lambda point: point['x'], reverse=True)


def contour_references(document):
    """Selectable mesh vertices, including an explicit owner at shared interfaces."""
    candidates = []
    for region in document.regions:
        ids = {document.element(ref)[key] for ref in region['boundary']
               if document.element(ref) for key in ('start_point', 'end_point')}
        candidates.extend((document.point(ref), region['id']) for ref in sorted(ids)
                          if document.point(ref) is not None)
    return candidates


def load_definition(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or 'geometry' not in data:
        raise ValueError('Keine Messschalen-Skizze: geometry fehlt.')
    document = sketch_document(data)
    document.ensure_regions_current()
    if not document.regions:
        document.detect_regions()
    result = document.clone_data()
    candidates = axis_references(document)
    if not candidates:
        raise ValueError('Ein äußerer Konturpunkt auf der x-Achse als Referenz wird benötigt.')
    metadata = result.setdefault('metadata', {})
    metadata.setdefault('alignment_point', candidates[0]['id'])
    metadata.setdefault('alignment_region', reference_region(result, 'alignment'))
    metadata.setdefault('sensor_tip_point', candidates[min(1, len(candidates) - 1)]['id'])
    metadata.setdefault('sensor_tip_region', reference_region(result))
    metadata.setdefault('units', 'mm')
    errors = definition_errors(result, require_drive=False)
    if errors:
        raise ValueError(' | '.join(errors))
    return result


def definition_errors(data, require_drive=True):
    try:
        document = sketch_document(data)
        # Validate the actual snapshot, not a silently normalized substitute.
        document.replace_data(data)
        errors = document.validate()
        violations = document._driving_dimension_violations()
        if violations:
            errors.append('Gespeicherte Geometrie verletzt Bemaßungen: ' + ', '.join(violations))
        if not document.regions:
            errors.append('Importierte Messschale benötigt zugewiesene Bereiche.')
        if data.get('metadata', {}).get('units', 'mm') != 'mm':
            errors.append('Die importierte Messschale muss in Millimetern definiert sein.')
        regions = {region['id'] for region in document.regions}
        from model.materials import MATERIALS
        for region in document.regions:
            domain_name(region['id'])
            material = MATERIALS.get(region.get('material'))
            if material is not None and material.fibers is not None:
                errors.append(f"{region['id']}: Faserorientierung ist für freie Messschalen noch nicht definiert.")
        counts = boundary_counts(document)
        if any(count > 2 for count in counts.values()):
            errors.append('Eine Randkante gehört zu mehr als zwei Bereichen.')
        candidates = axis_references(document)
        contour_ids = {point['id'] for point, _ in contour_references(document)}
        for role, label in (('alignment', 'Kontakt-Referenz'), ('sensor', 'Messpunkt')):
            metadata = data.get('metadata', {})
            reference = (metadata.get('alignment_point', metadata.get('sensor_tip_point')) if role == 'alignment'
                         else metadata.get('sensor_tip_point'))
            valid_ids = {point['id'] for point in candidates} if role == 'alignment' else contour_ids
            if reference not in valid_ids:
                location = 'äußeren Kontur/x-Achse' if role == 'alignment' else 'Bereichskontur'
                errors.append(f'{label} fehlt oder liegt nicht auf der {location}.')
            owner = reference_region(data, role)
            owner_region = next((region for region in document.regions if region['id'] == owner), None)
            if owner_region is None or not any(reference in (document.element(ref)['start_point'], document.element(ref)['end_point'])
                                               for ref in owner_region['boundary']):
                errors.append(f'{label} gehört nicht zum gewählten Messschalenbereich.')
        drive = [(region['id'], ref) for region in document.regions for ref in region['boundary']
                 if region.get('boundary_motion', {}).get(ref) == 'follow_translation']
        if require_drive and not drive:
            errors.append('Keine Zustellkante ausgewählt. Im Editor eine Kante auf vorgegebene Verschiebung setzen.')
        # Multiple frictionless/bonded junctions need domain-wise node splitting;
        # reject contradictory interface pairs instead of gluing them indirectly.
        bonded = {region: region for region in regions}
        def root(region):
            while bonded[region] != region:
                region = bonded[region]
            return region
        for interface in document.interfaces:
            if interface['condition'] == 'bonded':
                bonded[root(interface['region_a'])] = root(interface['region_b'])
        for interface in document.interfaces:
            if interface['condition'] == 'frictionless' and root(interface['region_a']) == root(interface['region_b']):
                errors.append('Reibungsfreie Bereiche sind über weitere verklebte Interfaces verbunden; diese Kombination ist noch nicht unterstützt.')
        return errors
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        return [f'Ungültige Messschalen-Datei: {error}']


def reference_x(data):
    reference = data['metadata'].get('alignment_point', data['metadata']['sensor_tip_point'])
    return next(point['x'] for point in data['geometry']['points'] if point['id'] == reference)


def reference_region(data, role='sensor'):
    metadata = data.get('metadata', {})
    region_key = 'alignment_region' if role == 'alignment' else 'sensor_tip_region'
    if metadata.get(region_key):
        return metadata[region_key]
    reference = (metadata.get('alignment_point', metadata.get('sensor_tip_point')) if role == 'alignment'
                 else metadata.get('sensor_tip_point'))
    edges = {edge['id']: edge for edge in data['geometry']['elements']}
    owners = [region for region in data['geometry']['regions']
              if any(reference in (edges[ref]['start_point'], edges[ref]['end_point'])
                     for ref in region['boundary'] if ref in edges)]
    if not owners:
        return None
    points = {point['id']: point for point in data['geometry']['points']}
    # If no explicit sensor owner was stored, prefer the less protruding body.
    owner = min(owners, key=lambda region: max(points[edges[ref][key]]['x'] for ref in region['boundary']
                                               for key in ('start_point', 'end_point')))
    return owner['id']


def preview_regions(data):
    from preprocessor_contours import boundary_samples
    document = sketch_document(data)
    offset = reference_x(data)
    return [(region, [(x - offset, max(0.0, y)) for x, y in boundary_samples(document, region['boundary'])])
            for region in document.regions]


def with_reference(data, point_id, region_id=None, role='alignment'):
    result = copy.deepcopy(data)
    point_key, region_key = (('alignment_point', 'alignment_region') if role == 'alignment'
                             else ('sensor_tip_point', 'sensor_tip_region'))
    result.setdefault('metadata', {})[point_key] = point_id
    result['metadata'].pop(region_key, None)
    result['metadata'][region_key] = region_id or reference_region(result, role)
    errors = definition_errors(result, require_drive=False)
    if errors:
        raise ValueError(' | '.join(errors))
    return result
