"""Closed faces, stale saved assignments and their actual Qt rendering."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from measurement_shell_prototype import CommandStack, MeasurementShellSketch, Window
from preprocessor_contours import boundary_samples


FIXTURE = Path(__file__).parent / 'fixtures' / 'measurement_shell_regions.json'


def rectangle(document, x1=0, x2=4, y1=0, y2=3, distinct=False, gap=0):
    coordinates = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    points = [document.add_point(*point) for point in coordinates]
    edges = []
    for index in range(4):
        start = points[index]
        end = points[(index + 1) % 4]
        if distinct:
            start = document.add_point(*coordinates[index])
            x, y = coordinates[(index + 1) % 4]
            end = document.add_point(x + (gap if index == 3 else 0), y)
        edges.append(document.add_line(start, end))
    return edges


class RegionTests(unittest.TestCase):
    def load_document(self):
        return MeasurementShellSketch(json.loads(FIXTURE.read_text(encoding='utf-8-sig')))

    def test_supplied_file_repairs_two_faces_without_changing_geometry(self):
        original = json.loads(FIXTURE.read_text(encoding='utf-8-sig'))
        document = MeasurementShellSketch(original)
        self.assertEqual(len(document.regions), 2)
        self.assertEqual(document.validate(), [])
        self.assertTrue(any('Bereichskonturen' in message for message in document.import_repairs))
        for key in ('points', 'elements', 'constraints', 'dimensions'):
            self.assertEqual(document.geometry[key], original['geometry'][key])
        self.assertEqual(document.data['parameters'], original['parameters'])
        self.assertEqual(original, json.loads(FIXTURE.read_text(encoding='utf-8-sig')))
        shell, insert = document.regions
        self.assertEqual(shell['id'], 'R001')
        self.assertEqual(insert['id'], 'R002')
        self.assertEqual(set(shell['boundary']), {'A001', 'L007', 'L006', 'L005', 'A004', 'A002', 'L011'})
        self.assertEqual(set(insert['boundary']), {'A005', 'L001', 'L002', 'L004', 'L008', 'L009', 'L010', 'L003', 'A002'})
        self.assertEqual(document.interfaces[0]['boundary_a'], ['A002'])
        self.assertEqual(document.interfaces[0]['boundary_b'], ['A002'])
        self.assertFalse(document.ensure_regions_current())
        self.assertEqual(document.detect_regions(), 2)

    def test_stale_assignments_preserve_material_behavior_and_contact(self):
        data = json.loads(FIXTURE.read_text(encoding='utf-8-sig'))
        first, second = data['geometry']['regions']
        from preprocessor_common import MATERIALS
        first['material'] = next(key for key in MATERIALS if key != second['material'])
        first['boundary_motion']['L006'] = 'follow_translation'
        data['geometry']['interfaces'][0]['condition'] = 'frictionless'
        document = MeasurementShellSketch(data)
        repaired = next(region for region in document.regions if region['id'] == first['id'])
        self.assertEqual(repaired['material'], first['material'])
        self.assertEqual(repaired['boundary_motion']['L006'], 'follow_translation')
        self.assertEqual(repaired['boundary_motion']['A002'], 'free_contact')
        self.assertEqual(document.interfaces[0]['condition'], 'frictionless')
        self.assertEqual(len(document.simulation_boundary_conditions()), 16)

    def test_missing_edges_rejected_but_do_not_block_rebuilding(self):
        document = self.load_document()
        document.regions[0]['boundary'][0] = 'A_missing'
        self.assertTrue(any('A_missing' in error for error in document.validate()))
        with self.assertRaises(ValueError):
            document.simulation_boundary_conditions()
        self.assertEqual(document.detect_regions(), 2)
        self.assertEqual(document.validate(), [])

    def test_interface_missing_edge_rejected_and_rebuilt(self):
        document = self.load_document()
        document.interfaces[0]['boundary_a'] = ['deleted']
        self.assertTrue(any('Interface' in error for error in document.validate()))
        self.assertTrue(document.ensure_regions_current())
        self.assertEqual(document.interfaces[0]['boundary_a'], ['A002'])

    def test_missing_interface_is_recreated(self):
        document = self.load_document()
        document.interfaces.clear()
        self.assertTrue(any('Interface fehlt' in error for error in document.validate()))
        self.assertTrue(document.ensure_regions_current())
        self.assertEqual(document.interfaces[0]['boundary_a'], ['A002'])

    def test_removed_interface_edge_merges_faces_without_arbitrary_material_migration(self):
        document = self.load_document()
        old_ids = {region['id'] for region in document.regions}
        history = CommandStack(document)
        before = document.clone_data()
        history.run('Trennkante löschen', lambda: document.delete_ids(['A002']))
        self.assertEqual(len(document.regions), 1)
        self.assertEqual(document.interfaces, [])
        self.assertNotIn(document.regions[0]['id'], old_ids)
        self.assertEqual(document.validate(), [])
        history.undo()
        self.assertEqual(document.data, before)

    def test_split_shared_arc_keeps_interface_and_both_regions_closed(self):
        document = self.load_document()
        arc = document.element('A002')
        import math
        center = document.point(arc['center_point'])
        angle = math.radians(arc['sweep_angle'] / 2)
        x = center['x'] + arc['radius'] * math.cos(angle)
        y = center['y'] + arc['radius'] * math.sin(angle)
        history = CommandStack(document)
        def split():
            point = document.add_point(x, y)
            document._split_element_at('A002', x, y, point)
        history.run('Bogen teilen', split)
        self.assertEqual(document.validate(), [])
        self.assertEqual(len(document.regions), 2)
        self.assertEqual(len(document.interfaces[0]['boundary_a']), 2)
        for region in document.regions:
            self.assertTrue(boundary_samples(document, region['boundary']))

    def test_canonical_coincident_endpoints_and_real_gap(self):
        document = MeasurementShellSketch()
        rectangle(document, distinct=True)
        self.assertEqual(document.detect_regions(), 1)
        samples = boundary_samples(document, document.regions[0]['boundary'])
        self.assertEqual(samples[0], samples[-1])
        document = MeasurementShellSketch()
        rectangle(document, distinct=True, gap=0.001)
        self.assertEqual(document.detect_regions(), 0)

    def test_internal_edge_creates_two_faces_not_outer_union(self):
        document = MeasurementShellSketch()
        a = document.add_point(0, 0); b = document.add_point(2, 0); c = document.add_point(4, 0)
        d = document.add_point(4, 3); e = document.add_point(2, 3); f = document.add_point(0, 3)
        for start, end in ((a, b), (b, c), (c, d), (d, e), (e, f), (f, a)):
            document.add_line(start, end)
        interface = document.add_line(b, e)
        self.assertEqual(document.detect_regions(), 2)
        self.assertEqual(document.interfaces[0]['boundary_a'], [interface])

    def test_construction_and_dangling_edges_not_material_boundaries(self):
        document = MeasurementShellSketch()
        edges = rectangle(document)
        a = document.element(edges[0])['start_point']
        b = document.add_point(2, 1)
        dangling = document.add_line(a, b)
        construction = document.add_line(a, document.element(edges[2])['start_point'], construction=True)
        self.assertEqual(document.detect_regions(), 1)
        self.assertEqual(set(document.regions[0]['boundary']), set(edges))
        self.assertNotIn(dangling, document.regions[0]['boundary'])
        self.assertNotIn(construction, document.regions[0]['boundary'])

    def test_arc_plus_line_is_a_valid_two_edge_face(self):
        document = MeasurementShellSketch()
        center = document.add_point(0, 0)
        start = document.add_point(2, 0); end = document.add_point(-2, 0)
        document.add_arc(center, start, end, 2)
        document.add_line(end, start)
        self.assertEqual(document.detect_regions(), 1)
        self.assertEqual(document.validate(), [])

    def test_split_inherits_explicit_behavior_and_history_is_consistent(self):
        document = self.load_document()
        document.regions[0]['boundary_motion']['L011'] = 'follow_translation'
        history = CommandStack(document)
        before = document.clone_data()
        edge = document.element('L011')
        a = document.point(edge['start_point']); b = document.point(edge['end_point'])
        x = (a['x'] + b['x']) / 2; y = a['y']
        def split():
            point = document.add_point(x, y)
            document._split_element_at('L011', x, y, point)
        history.run('Split', split)
        self.assertEqual(document.validate(), [])
        self.assertEqual(len(document.regions), 2)
        shell = next(region for region in document.regions if region['id'] == 'R001')
        new = next(ref for ref in shell['boundary'] if ref not in before['geometry']['regions'][0]['boundary'])
        self.assertEqual(shell['boundary_motion'][new], 'follow_translation')
        after = document.clone_data()
        history.undo(); self.assertEqual(document.data, before)
        history.redo(); self.assertEqual(document.data, after)

    def test_valid_file_roundtrip_has_no_further_repairs(self):
        document = self.load_document()
        restored = MeasurementShellSketch(document.clone_data())
        self.assertEqual(restored.import_repairs, [])
        self.assertEqual(restored.data, document.data)


class RegionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFontDatabase
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')

    def test_open_and_actual_region_paths_and_tab_switch(self):
        document = MeasurementShellSketch()
        window = Window(document, CommandStack(document))
        window.show(); self.app.processEvents()
        with patch('preprocessor_window_actions.QFileDialog.getOpenFileName', return_value=(str(FIXTURE), 'JSON')):
            window.load()
        self.assertEqual(window.region_list.count(), 2)
        self.assertEqual(window.interface_list.count(), 1)
        self.assertTrue(all(window._region_path(region['boundary']) is not None for region in document.regions))
        window.tabs.setCurrentIndex(1); self.app.processEvents()
        self.assertIn('2 geschlossene', window.status.text())
        self.assertEqual(window.region_list.count(), 2)
        self.assertEqual(document.validate(), [])
        # Verify contours actually appear as filled selectable region items.
        from PySide6.QtWidgets import QGraphicsPathItem
        filled = [item for item in window.region_scene.items()
                  if isinstance(item, QGraphicsPathItem) and item.zValue() == 1]
        self.assertEqual(len(filled), 2)
        window.close()


if __name__ == '__main__':
    unittest.main()
