from __future__ import annotations

import math

from preprocessor_common import *
from preprocessor_markers import point_marker, center_marker
from PySide6.QtCore import QPointF, QRectF, QTimer, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QBrush, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QLabel, QListWidgetItem,
    QInputDialog, QTableWidgetItem, QWidget, QFileDialog, QGraphicsEllipseItem, QGraphicsItem,
    QGraphicsLineItem, QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
    QMainWindow, QPushButton, QToolButton, QFrame, QStyle, QTabWidget, QTableWidget, QListWidget)


class WindowActionsMixin:
    def tangent_relation(self):
        ids = [element_id for element_id in self.selected_ids() if self.document.element(element_id)]
        if len(ids) != 2:
            self.status.setText("Tangential: bitte genau eine Linie und einen Kreisbogen auswählen (Strg-Klick).")
            return
        selected = [self.document.element(element_id) for element_id in ids]
        kinds = {element.get("kind") for element in selected if element is not None}
        if kinds != {"line", "arc"}:
            self.status.setText("Tangential: bitte genau eine Linie und einen Kreisbogen auswählen.")
            return
        try:
            self.mutate("Tangential", lambda: self.document.apply_tangency(ids))
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.refresh()
        self.status.setText("Tangentiale Beziehung erstellt; Bogen und Linie wurden am passenden Schnittpunkt angepasst.")

    def apply_coincident_references(self, references, extension=False):
        """Pass actual point, object and axis picks to the document resolver."""
        references = list(references or [])
        if len(references) != 2:
            self.status.setText('Coincident benötigt genau zwei Referenzen.')
            return
        try:
            self.mutate('Coincident', lambda: self.document.apply_coincident(references, extension=extension))
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.refresh()
        self.status.setText('Coincident-Beziehung erstellt.')

    def coincident_relation(self, extension=False):
        ids = [element_id for element_id in self.selected_ids() if self.document.element(element_id)]
        if len(ids) == 2:
            self.apply_coincident_references(ids, extension)
            return
        self.view.set_coincident_mode(extension)
        if len(ids) == 1:
            self.view.coincident_stage = 'second'
            self.view.coincident_data = {'first': ids[0]}
            self.status.setText('Coincident: zweite Referenz wählen (Punkt, Linie/Bogen oder Achse).')

    def fit_view(self):
        rect = self.view.scene().sceneRect()
        anchor = self.view.transformationAnchor()
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.fitInView(rect, Qt.KeepAspectRatio)
        self.view.centerOn(rect.center())
        self.view.setTransformationAnchor(anchor)

    def mutate(self, label, operation): self.history.run(label, operation)

    def refresh(self):
        self.list.blockSignals(True); self.list.clear()
        for item_data in self.document.elements + self.document.constraints + self.document.dimensions:
            item = QListWidgetItem(f"{item_data['id']}   {item_data.get('kind', item_data.get('type', ''))}")
            item.setData(Qt.ItemDataRole.UserRole, item_data["id"])
            self.list.addItem(item)
        self.list.addItems(f"VAR   {name} = {value.get('value', 0):.4g} {value.get('unit', 'mm')}" for name, value in self.document.data.get("parameters", {}).items())
        self.list.blockSignals(False)
        self.refresh_parameter_table(); self.refresh_relation_list(); self.refresh_region_ui(); self.draw(); self.selection_changed()

    def refresh_relation_list(self):
        if not hasattr(self, "relation_list"): return
        selected_geometry_ids = set(self.view.selected_element_ids)
        selected_geometry_ids.update(
            item.data(0) for item in self.view.scene().selectedItems()
            if self.document.element(item.data(0))
        )
        self.relation_list.clear()
        for constraint in self.document.constraints:
            relation = constraint.get("type", "Beziehung")
            reference_ids = list(constraint.get("references", []))
            display_ids = list(constraint.get("source_references", reference_ids))
            references = ", ".join(display_ids)
            label = {"horizontal": "Horizontal", "vertical": "Vertikal", "tangent": "Tangential", "coincident": "Coincident"}.get(relation, relation)
            if relation == "coincident" and constraint.get("mode") == "extension":
                label = "Coincident (Verlängerung)"
            item = QListWidgetItem(f"{constraint.get('id', '?')}   {label}: {references}")
            item.setData(Qt.ItemDataRole.UserRole, constraint.get("id"))
            item.setData(Qt.ItemDataRole.UserRole + 1, reference_ids)
            self.relation_list.addItem(item)
            item.setSelected(bool(selected_geometry_ids.intersection(set(reference_ids) | set(display_ids))))
        if not self.document.constraints:
            self.relation_list.addItem("Noch keine Geometriebeziehungen")

    def delete_selected_relations(self):
        if not hasattr(self, "relation_list"):
            return
        relation_ids = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.relation_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        }
        if not relation_ids:
            self.status.setText("Bitte eine oder mehrere Beziehungen auswählen.")
            return
        def operation():
            self.document.constraints[:] = [
                constraint for constraint in self.document.constraints
                if constraint.get("id") not in relation_ids
            ]
        self.mutate("Beziehungen löschen", operation)
        self.refresh()
        self.status.setText(f"{len(relation_ids)} Beziehung(en) gelöscht.")

    def refresh_parameter_table(self):
        if not hasattr(self, "parameter_table"): return
        self.parameter_table.blockSignals(True); self.parameter_table.setRowCount(0)
        for row, (name, parameter) in enumerate(self.document.data.get("parameters", {}).items()):
            self.parameter_table.insertRow(row)
            name_item = QTableWidgetItem(name); name_item.setData(Qt.ItemDataRole.UserRole, name); name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item = QTableWidgetItem(f"{parameter.get('value', 0):.6g}"); value_item.setData(Qt.ItemDataRole.UserRole, name); value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.parameter_table.setItem(row, 0, name_item); self.parameter_table.setItem(row, 1, value_item)
        self.parameter_table.blockSignals(False)

    def _edit_parameter_cell(self, row, column):
        if column != 1: return
        name = self.parameter_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        current = self.document.data.get("parameters", {}).get(name, {}).get("value", 1.0)
        value, ok = QInputDialog.getDouble(self, f"Parameter ändern: {name}", "Neuer Wert [mm]:", current, 0.001, 100000, 4)
        if ok:
            try: self.mutate("Parameter ändern", lambda: self.document.set_parameter(name, value)); self.refresh()
            except ValueError as error: self.status.setText(str(error))

    def refresh_region_ui(self):
        if not hasattr(self, "region_list"): return
        previous_boundary = self.selected_boundary
        self.region_list.blockSignals(True); self.region_list.clear()
        for region in self.document.regions:
            item = QListWidgetItem(f"{region['id']}   {region.get('name', 'Bereich')}   [{region.get('material', '')}]")
            item.setData(Qt.ItemDataRole.UserRole, region["id"]); self.region_list.addItem(item)
        self.region_list.blockSignals(False)
        if self.region_list.count():
            preferred_region = previous_boundary[0] if previous_boundary else None
            preferred_row = next((row for row in range(self.region_list.count())
                                  if self.region_list.item(row).data(Qt.ItemDataRole.UserRole) == preferred_region), 0)
            self.region_list.setCurrentRow(preferred_row)
        self.interface_list.clear()
        for interface in self.document.interfaces:
            item = QListWidgetItem(f"{interface['id']}   {interface['region_a']} ↔ {interface['region_b']}   ({interface.get('condition', 'bonded')})")
            item.setData(Qt.ItemDataRole.UserRole, interface["id"]); self.interface_list.addItem(item)

        if previous_boundary:
            boundary_item = next((self.boundary_list.item(row) for row in range(self.boundary_list.count())
                                  if self.boundary_list.item(row).data(Qt.ItemDataRole.UserRole) == previous_boundary), None)
            if boundary_item is not None:
                self.boundary_list.setCurrentItem(boundary_item)

    def _refresh_boundary_list(self, region):
        self.boundary_list.blockSignals(True); self.boundary_list.clear()
        motion_labels = {"follow_translation": "vorgegebene Verschiebung", "free_contact": "keine Verschiebungsvorgabe"}
        for element_id in region.get("boundary", []):
            element = self.document.element(element_id)
            if element is None: continue
            kind = "Linie" if element.get("kind") == "line" else "Bogen"
            behavior = region.setdefault("boundary_motion", {}).get(element_id, "free_contact")
            guidance = ' + nur x' if region.get('boundary_x_only', {}).get(element_id, False) else ''
            item = QListWidgetItem(f"{element_id}   {kind}   [{motion_labels.get(behavior, behavior)}{guidance}]")
            item.setData(Qt.ItemDataRole.UserRole, (region["id"], element_id)); self.boundary_list.addItem(item)
        self.boundary_list.blockSignals(False)
        if self.boundary_list.count(): self.boundary_list.setCurrentRow(0)

    def _region_selection_changed(self, current, previous):
        if current is None: return
        region = next((item for item in self.document.regions if item["id"] == current.data(Qt.ItemDataRole.UserRole)), None)
        if region is None: return
        index = self.region_material.findData(region.get("material"))
        if index >= 0: self.region_material.setCurrentIndex(index)
        self._refresh_boundary_list(region)

    def _boundary_selection_changed(self, current, previous):
        if current is None: return
        region_id, element_id = current.data(Qt.ItemDataRole.UserRole)
        self.selected_boundary = (region_id, element_id)
        region = next((item for item in self.document.regions if item.get("id") == region_id), None)
        if region is None: return
        behavior = region.get("boundary_motion", {}).get(element_id, "free_contact")
        index = self.boundary_behavior.findData(behavior)
        if index >= 0: self.boundary_behavior.setCurrentIndex(index)
        self.boundary_x_only.setChecked(region.get('boundary_x_only', {}).get(element_id, False))
        self.draw_region_view()

    def select_boundary(self, region_id, element_id):
        """Select a contour edge from the region canvas and sync the lists."""
        region = next((item for item in self.document.regions if item.get("id") == region_id), None)
        if region is None or element_id not in region.get("boundary", []): return
        self.selected_boundary = (region_id, element_id)
        region_item = next((self.region_list.item(row) for row in range(self.region_list.count())
                            if self.region_list.item(row).data(Qt.ItemDataRole.UserRole) == region_id), None)
        if region_item is not None:
            self.region_list.setCurrentItem(region_item)
        boundary_item = next((self.boundary_list.item(row) for row in range(self.boundary_list.count())
                              if self.boundary_list.item(row).data(Qt.ItemDataRole.UserRole) == (region_id, element_id)), None)
        if boundary_item is not None:
            self.boundary_list.setCurrentItem(boundary_item)
        self.status.setText(f"{element_id} ausgewählt. Randbedingung auswählen und setzen.")
        self.draw_region_view()

    def detect_regions(self):
        try:
            self.mutate('Bereiche erkennen', self.document.detect_regions)
            count = len(self.document.regions)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.refresh()
        self.status.setText(f"{count} geschlossene(r) Bereich(e) erkannt." if count else "Keine geschlossene Kontur erkannt.")

    def regions_tab_changed(self, index):
        if index != 1:
            return
        self.detect_regions()

    def apply_region_material(self):
        item = self.region_list.currentItem()
        if item is None: self.status.setText("Bitte zuerst einen Bereich auswählen."); return
        region = next((value for value in self.document.regions if value["id"] == item.data(Qt.ItemDataRole.UserRole)), None)
        if region is None: return
        material_key = self.region_material.currentData()
        self.mutate("Material zuweisen", lambda: region.update({"material": material_key})); self.refresh()

    def apply_boundary_behavior(self):
        item = self.boundary_list.currentItem()
        if item is None:
            self.status.setText("Bitte zuerst eine Randkante auswÃ¤hlen."); return
        region_id, element_id = item.data(Qt.ItemDataRole.UserRole)
        behavior = self.boundary_behavior.currentData()
        x_only = self.boundary_x_only.isChecked()
        def apply():
            self.document.set_boundary_motion(region_id, element_id, behavior)
            self.document.set_boundary_x_only(region_id, element_id, x_only)
        try:
            self.mutate("Randverhalten setzen", apply); self.refresh()
            self.status.setText("Randverhalten aktualisiert.")
        except ValueError as error:
            self.status.setText(str(error))

    def apply_interface_condition(self):
        item = self.interface_list.currentItem()
        if item is None: self.status.setText("Bitte zuerst ein Interface auswählen."); return
        interface = next((value for value in self.document.interfaces if value["id"] == item.data(Qt.ItemDataRole.UserRole)), None)
        if interface is None: return
        condition = self.interface_condition.currentData()
        self.mutate("Interface-Bedingung", lambda: interface.update({"condition": condition})); self.refresh()

    def content_rect(self):
        bounds = [(point["x"], -point["y"]) for point in self.document.points]
        for entity in self.document.elements:
            if entity["kind"] == "arc":
                center = self.document.point(entity["center_point"]); radius = entity["radius"]
                # Only the upper half-shell is legal, therefore do not
                # reserve view space for the forbidden lower semicircle.
                bounds.extend(((center["x"] - radius, -(center["y"] + radius)), (center["x"] + radius, -center["y"])))
        if not bounds:
            return QRectF(-10, -10, 20, 12)
        left = min(point[0] for point in bounds); right = max(point[0] for point in bounds)
        top = min(point[1] for point in bounds); bottom = max(point[1] for point in bounds)
        margin = max(4.0, 0.55 * max(right - left, bottom - top, 1.0))
        left -= margin; right += margin; top -= margin; bottom += margin
        if right - left < 20:
            middle = (left + right) / 2; left = middle - 10; right = middle + 10
        if bottom - top < 12:
            middle = (top + bottom) / 2; top = middle - 6; bottom = middle + 6
        return QRectF(left, top, right - left, bottom - top)

    def _region_path(self, boundary):
        from preprocessor_contours import boundary_samples
        try:
            samples = boundary_samples(self.document, boundary)
        except ValueError:
            return None
        path = QPainterPath(); path.moveTo(samples[0][0], -samples[0][1])
        for x, y in samples[1:]: path.lineTo(x, -y)
        path.closeSubpath()
        return path

    @staticmethod
    def _arc_path(center, start, radius, sweep, end=None):
        """Build an arc in data coordinates, then mirror y for the scene.

        QPainterPath.arcTo() uses a screen-angle convention that is easy to
        get wrong after the data y-axis is mirrored.  Sampling the exact
        circle makes the rendered endpoint identical to the preview marker.
        """
        start_angle = math.degrees(math.atan2(start["y"] - center["y"], start["x"] - center["x"]))
        steps = max(12, int(abs(sweep) / 5.0))
        path = QPainterPath()
        for index in range(steps + 1):
            if end is not None and index == steps:
                # Do not rely on the final floating-point angle to land on
                # the same pixel as the live endpoint marker.  The last
                # vertex is the actual model endpoint by definition.
                point = (end["x"], -end["y"])
            else:
                angle = math.radians(start_angle + sweep * index / steps)
                point = (center["x"] + radius * math.cos(angle),
                         -(center["y"] + radius * math.sin(angle)))
            if index == 0: path.moveTo(*point)
            else: path.lineTo(*point)
        return path

    def _dimension_pair_points(self, dimension):
        types = dimension.get("reference_types", [])
        refs = dimension.get("references", [])
        if len(types) != 2 or len(refs) != 2: return None
        resolved = []
        for reference_type, reference_id in zip(types, refs):
            resolved.append(self.document.point(reference_id) if reference_type == "point" else self.document.element(reference_id))
        if any(item is None for item in resolved): return None
        first_type, second_type = types
        first, second = resolved
        if first_type == "point" and second_type == "point":
            return (first, second)
        if first_type == "element" and second_type == "point":
            line, point = first, second
            reverse = False
        elif first_type == "point" and second_type == "element":
            point, line = first, second
            reverse = True
        elif first_type == "element" and second_type == "element" and first.get("kind") == "line" and second.get("kind") == "line":
            first_a = self.document.point(first["start_point"]); first_b = self.document.point(first["end_point"])
            second_a = self.document.point(second["start_point"]); second_b = self.document.point(second["end_point"])
            if dimension.get("axis") == "vertical":
                x = (first_a["x"] + first_b["x"] + second_a["x"] + second_b["x"]) / 4.0
                return ({"x": x, "y": first_a["y"]}, {"x": x, "y": second_a["y"]})
            y = (first_a["y"] + first_b["y"] + second_a["y"] + second_b["y"]) / 4.0
            return ({"x": first_a["x"], "y": y}, {"x": second_a["x"], "y": y})
        else:
            return None
        if line.get("kind") != "line": return None
        start = self.document.point(line["start_point"]); end = self.document.point(line["end_point"])
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        length_squared = dx * dx + dy * dy
        if length_squared <= 1e-12: return None
        t = max(0.0, min(1.0, ((point["x"] - start["x"]) * dx + (point["y"] - start["y"]) * dy) / length_squared))
        projection = {"x": start["x"] + t * dx, "y": start["y"] + t * dy}
        return (point, projection) if reverse else (projection, point)

    def _dynamic_dimension(self):
        stage = self.view.dimension_stage; data = self.view.dimension_data; cursor = self.view.preview_point
        if not stage or cursor is None: return None
        if stage == "element_place":
            entity = self.document.element(data.get("element_id"))
            if entity is None: return None
            if entity["kind"] == "arc":
                center = self.document.point(entity["center_point"])
                return {"type": "radius", "references": [entity["id"]], "value": float(entity["radius"]),
                        "display_position": [cursor.x(), cursor.y()]}
            start = self.document.point(entity["start_point"]); end = self.document.point(entity["end_point"])
            dx, dy = end["x"] - start["x"], end["y"] - start["y"]
            if entity["orientation"] == "horizontal":
                axis = "horizontal"
            elif entity["orientation"] == "vertical":
                axis = "vertical"
            else:
                midpoint_x = (start["x"] + end["x"]) / 2.0
                midpoint_y = (start["y"] + end["y"]) / 2.0
                axis = "horizontal" if abs(cursor.y() - midpoint_y) >= abs(cursor.x() - midpoint_x) else "vertical"
            if axis == "horizontal":
                value = abs(dx); position = [(start["x"] + end["x"]) / 2.0, cursor.y()]
            else:
                value = abs(dy); position = [cursor.x(), (start["y"] + end["y"]) / 2.0]
            return {"type": "length", "references": [entity["id"]], "value": value,
                    "display_position": position, "axis": axis}
        if stage == "pair_place":
            types = [data.get("first_type"), data.get("second_type")]
            refs = [data.get("first_id"), data.get("second_id")]
            if any(reference is None for reference in refs): return None
            first = self.document.point(refs[0]) if types[0] == "point" else self.document.element(refs[0])
            second = self.document.point(refs[1]) if types[1] == "point" else self.document.element(refs[1])
            if first is None or second is None: return None
            if types == ["element", "element"]:
                if first.get("kind") != "line" or second.get("kind") != "line": return None
                if first.get("orientation") == "horizontal" and second.get("orientation") == "horizontal": axis = "vertical"
                elif first.get("orientation") == "vertical" and second.get("orientation") == "vertical": axis = "horizontal"
                else: return None
            elif types == ["element", "point"] or types == ["point", "element"]:
                line = first if types[0] == "element" else second
                if line.get("kind") != "line": return None
                if line.get("orientation") == "horizontal": axis = "vertical"
                elif line.get("orientation") == "vertical": axis = "horizontal"
                else:
                    midpoint = self._dimension_pair_points({"references": refs, "reference_types": types})
                    if midpoint is None: return None
                    mid_x = sum(item["x"] for item in midpoint) / 2.0; mid_y = sum(item["y"] for item in midpoint) / 2.0
                    axis = "horizontal" if abs(cursor.y() - mid_y) < abs(cursor.x() - mid_x) else "vertical"
            else:
                midpoint = self._dimension_pair_points({"references": refs, "reference_types": types})
                if midpoint is None: return None
                mid_x = sum(item["x"] for item in midpoint) / 2.0; mid_y = sum(item["y"] for item in midpoint) / 2.0
                axis = "horizontal" if abs(cursor.y() - mid_y) >= abs(cursor.x() - mid_x) else "vertical"
            points = self._dimension_pair_points({"references": refs, "reference_types": types, "axis": axis})
            if points is None: return None
            first_point, second_point = points
            value = abs((second_point["x"] - first_point["x"]) if axis == "horizontal" else (second_point["y"] - first_point["y"]))
            position = [(first_point["x"] + second_point["x"]) / 2.0, cursor.y()] if axis == "horizontal" else [cursor.x(), (first_point["y"] + second_point["y"]) / 2.0]
            return {"type": "distance", "references": refs, "reference_types": types, "value": value,
                    "display_position": position, "axis": axis}
        if stage == "point_place":
            first = self.document.point(data.get("point_id")); kind = data.get("kind")
            if first is None: return None
            if kind in ("point_horizontal", "point_vertical"):
                value = abs(first["x"]) if kind == "point_horizontal" else abs(first["y"])
                position = [cursor.x(), first["y"]] if kind == "point_vertical" else [first["x"], cursor.y()]
                return {"type": kind, "references": [first["id"]], "value": value, "display_position": position}
            second = self.document.point(data.get("second_id"))
            if second is None: return None
            if kind == "horizontal_distance":
                value = abs(second["x"] - first["x"]); position = [(first["x"] + second["x"]) / 2.0, cursor.y()]
            else:
                value = abs(second["y"] - first["y"]); position = [cursor.x(), (first["y"] + second["y"]) / 2.0]
            return {"type": kind, "references": [first["id"], second["id"]], "value": value, "display_position": position}
        return None

    def choose_dimension_parameter(self, value):
        choices = ["(kein Parameter)"] + list(self.document.data.get("parameters", {})) + ["+ Neuer Parameter"]
        choice, ok = QInputDialog.getItem(self, "Bemaßung erstellen", "Parameter für dieses Maß:", choices, 0, False)
        if not ok: return False, None, False
        if choice == "(kein Parameter)": return True, None, False
        if choice == "+ Neuer Parameter":
            name, name_ok = QInputDialog.getText(self, "Neuer Geometrieparameter", "Name (z. B. R_outer):")
            if not name_ok or not name.strip(): return False, None, False
            name = name.strip()
            if name in self.document.data.get("parameters", {}):
                self.status.setText("Dieser Parameter existiert bereits."); return False, None, False
            return True, name, True
        return True, choice, False

    def commit_dynamic_dimension(self):
        dimension = self._dynamic_dimension()
        if dimension is None or dimension["value"] <= 1e-9:
            self.status.setText("Bemaßung ist zu klein oder ungültig."); return
        accepted, parameter, is_new = self.choose_dimension_parameter(dimension["value"])
        if not accepted: return
        created = []
        def operation():
            target = dimension['value']
            if parameter:
                if is_new: self.document.add_parameter(parameter, dimension["value"])
                else: target = self.document.data['parameters'][parameter]['value']
            created.append(self.document.add_dimension(dimension["type"], dimension["references"], target, parameter=parameter,
                                   display_position=dimension["display_position"], axis=dimension.get("axis"),
                                   reference_types=dimension.get("reference_types")))
            if parameter:
                self.document.set_parameter(parameter, target)
        try:
            self.mutate("Bemaßung erstellen", operation)
        except ValueError as error:
            self.status.setText(str(error)); return
        self.view.dimension_stage, self.view.dimension_data, self.view.preview_point = None, {}, None
        self.refresh()
        if parameter is None and created:
            dimension_id = created[0]
            created_dimension = self.document.dimension(dimension_id)
            adjusted_value, adjust_ok = QInputDialog.getDouble(
                self,
                "Bemaessung anpassen",
                f"Neuer Wert fuer {created_dimension['type']} [mm]:",
                float(created_dimension["value"]),
                0.001,
                100000,
                4,
            )
            if adjust_ok and abs(adjusted_value - created_dimension["value"]) > 1e-12:
                try:
                    self.mutate("Bemaessung anpassen",
                                lambda: self.document.set_dimension(dimension_id, adjusted_value))
                    self.refresh()
                except ValueError as error:
                    self.status.setText(str(error))
        self.status.setText("BemaÃŸung ohne Parameter erstellt." if parameter is None else f"BemaÃŸung {created[0]} erstellt.")

    def _dimension_line(self, scene, x1, y1, x2, y2, pen):
        item = scene.addLine(x1, -y1, x2, -y2, pen); item.setZValue(8)
        size = 0.12
        if abs(y2 - y1) <= abs(x2 - x1):
            for x, direction in ((x1, 1), (x2, -1)):
                scene.addLine(x, -y1, x + direction * size, -y1 - size / 2, pen).setZValue(8)
                scene.addLine(x, -y1, x + direction * size, -y1 + size / 2, pen).setZValue(8)
        else:
            for y, direction in ((y1, 1), (y2, -1)):
                scene.addLine(x1, -y, x1 + size / 2, -y - direction * size, pen).setZValue(8)
                scene.addLine(x1, -y, x1 - size / 2, -y - direction * size, pen).setZValue(8)

    def _dimension_arrow(self, scene, x, y, direction_x, direction_y, pen):
        """Draw one SolidWorks-like filled-looking V arrow at a radial tip."""
        length = math.hypot(direction_x, direction_y)
        if length <= 1e-9:
            return
        ux, uy = direction_x / length, direction_y / length
        px, py = -uy, ux
        size = 0.12
        half = size * 0.45
        base_x, base_y = x - ux * size, y - uy * size
        for bx, by in ((base_x + px * half, base_y + py * half),
                       (base_x - px * half, base_y - py * half)):
            scene.addLine(x, -y, bx, -by, pen).setZValue(8)

    def _draw_dimension(self, scene, dimension, preview=False):
        kind = dimension["type"]; refs = dimension.get("references", []); position = dimension.get("display_position", [0, 0]); pen = QPen(QColor("#e6bd4f" if not preview else "#d58b28"), 1.0, Qt.DashLine if preview else Qt.SolidLine); pen.setCosmetic(True)
        suffix = f"  ({dimension['parameter']})" if dimension.get("parameter") else ""
        label = QGraphicsSimpleTextItem(("R" if kind == "radius" else "") + f"{dimension['value']:.3g} mm" + suffix); label.setBrush(QColor("#f0a43c" if dimension.get("id") in self.view.selected_dimension_ids else "#c29220")); label.setZValue(9); label.setData(0, dimension.get("id")); label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); label.setSelected(dimension.get("id") in self.view.selected_dimension_ids); label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        if kind == "radius":
            entity = self.document.element(refs[0]); center = self.document.point(entity["center_point"]); x, y = position
            direction_x, direction_y = x - center["x"], y - center["y"]
            distance = math.hypot(direction_x, direction_y)
            if distance <= 1e-9:
                endpoint = self.document.point(entity["end_point"])
                direction_x, direction_y = endpoint["x"] - center["x"], endpoint["y"] - center["y"]
                distance = math.hypot(direction_x, direction_y)
            radius = float(entity["radius"])
            ux, uy = direction_x / distance, direction_y / distance
            arrow_x, arrow_y = center["x"] + radius * ux, center["y"] + radius * uy
            line_distance = max(distance, radius)
            line_x, line_y = center["x"] + line_distance * ux, center["y"] + line_distance * uy
            scene.addLine(center["x"], -center["y"], line_x, -line_y, pen).setZValue(8)
            self._dimension_arrow(scene, arrow_x, arrow_y, ux, uy, pen)
            label.setPos(x, -y); scene.addItem(label); return
        if kind == "length":
            entity = self.document.element(refs[0]); start = self.document.point(entity["start_point"]); end = self.document.point(entity["end_point"])
            axis = dimension.get("axis") or entity.get("orientation")
            if axis not in ("horizontal", "vertical"):
                midpoint_x = (start["x"] + end["x"]) / 2.0; midpoint_y = (start["y"] + end["y"]) / 2.0
                axis = "horizontal" if abs(position[1] - midpoint_y) >= abs(position[0] - midpoint_x) else "vertical"
            if axis == "horizontal":
                dim_y = position[1]; scene.addLine(start["x"], -start["y"], start["x"], -dim_y, pen).setZValue(8); scene.addLine(end["x"], -end["y"], end["x"], -dim_y, pen).setZValue(8); self._dimension_line(scene, start["x"], dim_y, end["x"], dim_y, pen); label.setPos((start["x"] + end["x"]) / 2.0, -dim_y)
            else:
                dim_x = position[0]; scene.addLine(start["x"], -start["y"], dim_x, -start["y"], pen).setZValue(8); scene.addLine(end["x"], -end["y"], dim_x, -end["y"], pen).setZValue(8); self._dimension_line(scene, dim_x, start["y"], dim_x, end["y"], pen); label.setPos(dim_x, -(start["y"] + end["y"]) / 2.0)
        elif kind == "distance":
            points = self._dimension_pair_points(dimension); axis = dimension.get("axis")
            if points is not None and axis == "horizontal":
                first, second = points; dim_y = position[1]
                scene.addLine(first["x"], -first["y"], first["x"], -dim_y, pen).setZValue(8); scene.addLine(second["x"], -second["y"], second["x"], -dim_y, pen).setZValue(8)
                self._dimension_line(scene, first["x"], dim_y, second["x"], dim_y, pen); label.setPos((first["x"] + second["x"]) / 2.0, -dim_y)
            elif points is not None and axis == "vertical":
                first, second = points; dim_x = position[0]
                scene.addLine(first["x"], -first["y"], dim_x, -first["y"], pen).setZValue(8); scene.addLine(second["x"], -second["y"], dim_x, -second["y"], pen).setZValue(8)
                self._dimension_line(scene, dim_x, first["y"], dim_x, second["y"], pen); label.setPos(dim_x, -(first["y"] + second["y"]) / 2.0)
        elif kind in ("point_horizontal", "point_vertical"):
            point = self.document.point(refs[0])
            if kind == "point_horizontal":
                dim_y = position[1]; scene.addLine(point["x"], -point["y"], point["x"], -dim_y, pen).setZValue(8); self._dimension_line(scene, 0, dim_y, point["x"], dim_y, pen); label.setPos(point["x"] / 2.0, -dim_y)
            else:
                dim_x = position[0]; scene.addLine(point["x"], -point["y"], dim_x, -point["y"], pen).setZValue(8); self._dimension_line(scene, dim_x, 0, dim_x, point["y"], pen); label.setPos(dim_x, -point["y"] / 2.0)
        elif kind in ("horizontal_distance", "vertical_distance") and len(refs) >= 2:
            first = self.document.point(refs[0]); second = self.document.point(refs[1])
            if kind == "horizontal_distance":
                dim_y = position[1]; scene.addLine(first["x"], -first["y"], first["x"], -dim_y, pen).setZValue(8); scene.addLine(second["x"], -second["y"], second["x"], -dim_y, pen).setZValue(8); self._dimension_line(scene, first["x"], dim_y, second["x"], dim_y, pen); label.setPos((first["x"] + second["x"]) / 2.0, -dim_y)
            else:
                dim_x = position[0]; scene.addLine(first["x"], -first["y"], dim_x, -first["y"], pen).setZValue(8); scene.addLine(second["x"], -second["y"], dim_x, -second["y"], pen).setZValue(8); self._dimension_line(scene, dim_x, first["y"], dim_x, second["y"], pen); label.setPos(dim_x, -(first["y"] + second["y"]) / 2.0)
        scene.addItem(label)

    def draw_region_view(self):
        if not hasattr(self, "region_scene"): return
        scene = self.region_scene; scene.clear(); rect = self.content_rect(); scene.setSceneRect(rect); scene.setBackgroundBrush(QColor("#e7e8ea"))
        if not self.document.regions:
            text = QGraphicsSimpleTextItem("Noch keine geschlossenen Bereiche erkannt")
            text.setBrush(QColor("#70757a")); text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True); text.setPos(rect.center()); scene.addItem(text); self.region_view.fitInView(rect, Qt.KeepAspectRatio); return
        colors = (QColor(91, 161, 214, 170), QColor(226, 164, 83, 175), QColor(126, 184, 128, 170), QColor(177, 137, 204, 170))
        for index, region in enumerate(self.document.regions):
            path = self._region_path(region.get("boundary", []))
            if path is None: continue
            outline = QPen(QColor("#365d7c"), 1.2); outline.setCosmetic(True)
            fill = QGraphicsPathItem(path); fill.setBrush(QBrush(colors[index % len(colors)])); fill.setPen(outline); fill.setData(0, region["id"]); fill.setZValue(1); scene.addItem(fill)
            motion = region.setdefault("boundary_motion", {})
            for element_id in region.get("boundary", []):
                element = self.document.element(element_id)
                if element is None: continue
                selected = self.selected_boundary == (region["id"], element_id)
                behavior = motion.get(element_id, "free_contact")
                x_only = region.get('boundary_x_only', {}).get(element_id, False)
                edge_color = '#2b8a57' if behavior == 'follow_translation' else ('#8658b8' if x_only else '#d17a22')
                edge_pen = QPen(QColor("#f0a43c") if selected else QColor(edge_color), 3.4 if selected else 2.2)
                edge_pen.setCosmetic(True)
                if element["kind"] == "line":
                    start = self.document.point(element["start_point"]); end = self.document.point(element["end_point"])
                    edge = scene.addLine(start["x"], -start["y"], end["x"], -end["y"], edge_pen)
                else:
                    center = self.document.point(element["center_point"]); start = self.document.point(element["start_point"])
                    end = self.document.point(element["end_point"])
                    edge = QGraphicsPathItem(self._arc_path(center, start, element["radius"], element.get("sweep_angle", 0.0), end=end))
                    edge.setPen(edge_pen); scene.addItem(edge)
                edge.setData(0, region["id"]); edge.setData(1, (region["id"], element_id)); edge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); edge.setZValue(3)
                edge.setToolTip(f"{region['id']} / {element_id}: " + ('ux vorgegeben' if behavior == 'follow_translation' else 'ux ohne Vorgabe') + ('; nur x (uy=uz=0)' if x_only else '; keine zusätzliche Querfixierung'))
            points = []
            for element_id in region.get("boundary", []):
                element = self.document.element(element_id)
                if element is None: continue
                for reference in (element.get("start_point"), element.get("end_point")):
                    point = self.document.point(reference)
                    if point: points.append((point["x"], -point["y"]))
            if points:
                x = sum(point[0] for point in points) / len(points); y = sum(point[1] for point in points) / len(points)
                label = QGraphicsSimpleTextItem(f"{region.get('name', region['id'])}\n{region.get('material', '')}")
                label.setBrush(QColor("#203040")); label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True); label.setPos(x, y); label.setZValue(2); scene.addItem(label)
        self.region_view.fitInView(rect, Qt.KeepAspectRatio)

    def draw(self):
        scene = self.view.scene(); selected_ids = set(self.view.selected_element_ids) | {i.data(0) for i in scene.selectedItems() if self.document.element(i.data(0))}; selected_dimension_ids = set(self.view.selected_dimension_ids) | {i.data(0) for i in scene.selectedItems() if self.document.dimension(i.data(0))}; self.view.selected_element_ids = {item_id for item_id in selected_ids if self.document.element(item_id)}; self.view.selected_dimension_ids = {item_id for item_id in selected_dimension_ids if self.document.dimension(item_id)}; scene.clear(); scene.setSceneRect(self.content_rect()); scene.setBackgroundBrush(QColor("#e7e8ea"))
        grid = QPen(QColor("#d7d9dc"), 0.6); axis_x = QPen(QColor("#333333"), 1.0, Qt.DashLine); axis_y = QPen(QColor("#3c8c4a"), 1.0); construction = QPen(QColor("#777777"), 1.0, Qt.DashLine); normal = QPen(QColor("#123de0"), 1.6); selected = QPen(QColor("#ff8c00"), 2.2)
        for pen in (grid, axis_x, axis_y, construction, normal, selected): pen.setCosmetic(True)
        rect = scene.sceneRect(); left = math.floor(rect.left() / 10) * 10; right = math.ceil(rect.right() / 10) * 10
        top = math.floor(rect.top() / 10) * 10; bottom = math.ceil(rect.bottom() / 10) * 10
        for x in range(int(left), int(right) + 1, 10): scene.addLine(x, top, x, bottom, grid)
        for y in range(int(top), int(bottom) + 1, 10): scene.addLine(left, y, right, y, grid)
        if top <= 0 <= bottom: scene.addLine(left, 0, right, 0, axis_x)
        if top <= 0 <= bottom: scene.addLine(0, top, 0, 0, axis_y)
        for index, region in enumerate(self.document.regions):
            path = self._region_path(region.get("boundary", []))
            if path is None: continue
            fill = QGraphicsPathItem(path); fill.setBrush(QBrush(QColor(170, 166, 207, 155 if index == 0 else 125))); fill.setPen(QPen(Qt.NoPen)); fill.setZValue(-2); fill.setData(0, region["id"]); scene.addItem(fill)
        for entity in self.document.elements:
            pen = selected if entity["id"] in selected_ids else (construction if entity.get("construction") else normal)
            if entity["kind"] == "line":
                a, b = self.document.point(entity["start_point"]), self.document.point(entity["end_point"]); item = QGraphicsLineItem(a["x"], -a["y"], b["x"], -b["y"])
            else:
                c = self.document.point(entity["center_point"]); start = self.document.point(entity["start_point"]); end = self.document.point(entity["end_point"]); r = entity["radius"]
                sweep = float(entity.get("sweep_angle", default_arc_sweep(c, start, end)))
                item = QGraphicsPathItem(self._arc_path(c, start, r, sweep, end=end))
            item.setPen(pen); item.setData(0, entity["id"]); item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True); item.setSelected(entity["id"] in selected_ids); scene.addItem(item)
        for dimension in self.document.dimensions:
            self._draw_dimension(scene, dimension)
        self.draw_preview(scene)
        self.draw_point_markers(scene)
        self.draw_region_view()

    def draw_point_markers(self, scene):
        centers = set()
        for entity in self.document.elements:
            if entity["kind"] != "arc": continue
            center = self.document.point(entity["center_point"])
            if center is None or center['id'] in centers: continue
            centers.add(center['id'])
            marker = center_marker(scene, center['x'], center['y'])
            marker.setData(2, ('center', center['id']))
        seen = set()
        for entity in self.document.elements:
            references = [entity["start_point"], entity["end_point"]]
            for reference in references:
                if reference in seen: continue
                point = self.document.point(reference)
                if point is None: continue
                seen.add(reference)
                marker = point_marker(scene, point['x'], point['y'])
                marker.setData(2, ('endpoint', reference))
                marker.setToolTip(reference)
        if self.view.snap_target is not None:
            target = self.view.snap_target
            point_marker(scene, target.x(), target.y(), radius=3.0, color='#18a558', fill='#bef5cd', z=21)

    def draw_preview(self, scene):
        dynamic_dimension = self._dynamic_dimension()
        if dynamic_dimension is not None:
            self._draw_dimension(scene, dynamic_dimension, preview=True)
            return
        points = self.view.points; cursor = self.view.preview_point
        if self.view.mode == "line" and not points and cursor is not None:
            point_marker(scene, cursor.x(), cursor.y(), color='#165dff', z=25)
            return
        if self.view.mode == "arc" and not points and cursor is not None:
            if self.view.pointer_point is not None:
                pointer = self.view.pointer_point
                if math.hypot(pointer.x() - cursor.x(), pointer.y() - cursor.y()) > 1e-6:
                    guide = QPen(QColor("#8a9299"), 1.0, Qt.DotLine); guide.setCosmetic(True)
                    scene.addLine(pointer.x(), -pointer.y(), cursor.x(), -cursor.y(), guide)
            point_marker(scene, cursor.x(), cursor.y(), color='#165dff', z=25)
            return
        if not points or cursor is None: return
        pen = QPen(QColor("#165dff"), 1.6, Qt.DashLine); pen.setCosmetic(True)
        point_marker(scene, cursor.x(), cursor.y(), color='#165dff', z=25)
        if self.view.mode == "line":
            start = points[-1]; scene.addLine(start.x(), -start.y(), cursor.x(), -cursor.y(), pen)
            return
        if self.view.mode != "arc": return
        center = points[0]
        if len(points) == 1:
            scene.addLine(center.x(), -center.y(), cursor.x(), -cursor.y(), pen)
            radius = math.hypot(cursor.x()-center.x(), cursor.y()-center.y())
            scene.addEllipse(center.x()-radius, -center.y()-radius, 2*radius, 2*radius, pen)
            return
        start = points[1]; radius = math.hypot(start.x()-center.x(), start.y()-center.y())
        if radius <= 1e-9: return
        if self.view.pointer_point is not None:
            pointer = self.view.pointer_point
            if math.hypot(pointer.x() - cursor.x(), pointer.y() - cursor.y()) > 1e-6:
                guide = QPen(QColor("#8a9299"), 1.0, Qt.DotLine); guide.setCosmetic(True)
                scene.addLine(pointer.x(), -pointer.y(), cursor.x(), -cursor.y(), guide)
        sweep = default_arc_sweep({"x": center.x(), "y": center.y()}, {"x": start.x(), "y": start.y()}, {"x": cursor.x(), "y": cursor.y()})
        path = self._arc_path(
            {"x": center.x(), "y": center.y()},
            {"x": start.x(), "y": start.y()},
            radius,
            sweep,
            end={"x": cursor.x(), "y": cursor.y()},
        )
        scene.addPath(path, pen)

    def selected_ids(self):
        # Keep one canonical selection model.  QGraphicsScene selection can
        # be rebuilt during redraw, so it is only a fallback/synchronizer;
        # stale ids must never reach relation or edit commands.
        ids = [item_id for item_id in self.view.selected_element_ids if self.document.element(item_id)]
        self.view.selected_element_ids = set(ids)
        dimension_ids = [item_id for item_id in self.view.selected_dimension_ids if self.document.dimension(item_id)]
        self.view.selected_dimension_ids = set(dimension_ids)
        ids.extend(item_id for item_id in dimension_ids if item_id not in ids)
        for item in self.view.scene().selectedItems():
            item_id = item.data(0)
            if item_id and self.document.element(item_id) and item_id not in ids:
                ids.append(item_id)
                self.view.selected_element_ids.add(item_id)
            elif item_id and self.document.dimension(item_id) and item_id not in ids:
                ids.append(item_id)
                self.view.selected_dimension_ids.add(item_id)
        return ids

    def feature_list_selection_changed(self):
        selected = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        }
        self.view.selected_element_ids = {item_id for item_id in selected if self.document.element(item_id)}
        self.view.selected_dimension_ids = {item_id for item_id in selected if self.document.dimension(item_id)}
        for item in self.view.scene().items():
            item_id = item.data(0)
            if self.document.element(item_id) or self.document.dimension(item_id):
                item.setSelected(item_id in self.view.selected_element_ids or item_id in self.view.selected_dimension_ids)
        self.selection_changed()

    def selection_changed(self):
        selected_ids = set(self.selected_ids())
        for item in self.view.scene().items():
            item_id = item.data(0)
            if not item_id: continue
            entity = self.document.element(item_id)
            dimension = self.document.dimension(item_id)
            if item_id in selected_ids:
                if dimension:
                    item.setBrush(QColor("#f0a43c"))
                else:
                    pen = QPen(QColor("#f0a43c"), 2.0); pen.setCosmetic(True); item.setPen(pen)
            elif entity:
                pen = QPen(QColor("#888888"), 0.9, Qt.DashLine) if entity.get("construction") else QPen(QColor("#123de0"), 1.6)
                pen.setCosmetic(True); item.setPen(pen)
            elif dimension:
                item.setBrush(QColor("#c29220"))
        self.status.setText(f"{len(selected_ids)} Element(e) ausgewählt | Skizze: parametrische Vorschau")
        self.refresh_relation_list()

    def delete_selected(self):
        ids = self.selected_ids()
        if ids: self.mutate("Elemente löschen", lambda: self.document.delete_ids(ids)); self.refresh()

    def apply_relation(self, kind):
        if kind not in ("horizontal", "vertical"):
            self.status.setText("Unbekannte Linienbedingung."); return
        ids = [item_id for item_id in self.selected_ids() if self.document.element(item_id)]
        entity = self.document.element(ids[0]) if len(ids) == 1 else None
        if not entity or entity["kind"] != "line": self.status.setText("Bitte genau eine Linie auswählen."); return
        def operation():
            self.document.constrain_line(entity['id'], kind)
        try:
            self.mutate(kind.capitalize(), operation)
        except ValueError as error:
            self.status.setText(str(error)); return
        self.refresh(); self.status.setText(f"{kind.capitalize()}e Bedingung erstellt.")

    def smart_dimension(self):
        ids = self.selected_ids(); entity = self.document.element(ids[0]) if len(ids) == 1 else None
        if not entity: return
        if entity["kind"] == "arc": self.radius_dimension(); return
        if entity["kind"] != "line": self.status.setText("Für dieses Element ist noch keine intelligente Bemaßung verfügbar."); return
        start = self.document.point(entity["start_point"]); end = self.document.point(entity["end_point"])
        old = math.hypot(end["x"]-start["x"], end["y"]-start["y"])
        value, ok = QInputDialog.getDouble(self, "Linie bemaßen", "Länge [mm]:", old, 0.001, 100000, 4)
        if ok:
            def operation():
                dimension_id = self.document.add_dimension("length", [entity["id"]], old,
                                                       display_position=[(start["x"]+end["x"])/2, (start["y"]+end["y"])/2])
                self.document.set_dimension(dimension_id, value)
            self.mutate("Linie bemaßen", operation); self.refresh()

    def radius_dimension(self):
        ids = self.selected_ids(); entity = self.document.element(ids[0]) if len(ids) == 1 else None
        if not entity or entity["kind"] != "arc": self.status.setText("Bitte genau einen Kreisbogen auswählen."); return
        value, ok = QInputDialog.getDouble(self, "Radius bemaßen", "Radius [mm]:", entity["radius"], 0.001, 100000, 4)
        if ok:
            center = self.document.point(entity["center_point"])
            existing = next((dimension for dimension in self.document.dimensions
                             if dimension.get("type") == "radius" and entity["id"] in dimension.get("references", [])), None)
            def operation():
                if existing is not None:
                    self.document.set_dimension(existing["id"], value)
                else:
                    dimension_id = self.document.add_dimension("radius", [entity["id"]], entity["radius"],
                                                          display_position=[center["x"] + value, center["y"]])
                    self.document.set_dimension(dimension_id, value)
            self.mutate("Radius bemaßen", operation); self.refresh()

    def edit_dimension(self):
        dimension_ids = [item_id for item_id in self.view.selected_dimension_ids
                         if self.document.dimension(item_id)]
        if len(dimension_ids) == 1:
            dimension = self.document.dimension(dimension_ids[0])
        elif len(self.document.dimensions) == 1:
            # A single dimension can be edited even if its label lost
            # selection during a redraw or focus change.
            dimension = self.document.dimensions[0]
        elif len(self.document.dimensions) > 1:
            choices = [f"{item['id']}  {item['type']}  {item['value']:.4g} mm"
                       for item in self.document.dimensions]
            choice, ok = QInputDialog.getItem(self, "BemaÃŸung auswÃ¤hlen",
                                               "BemaÃŸung:", choices, 0, False)
            if not ok: return
            dimension = self.document.dimensions[choices.index(choice)]
        else:
            dimension = None
        if dimension is None:
            self.status.setText("Bitte genau eine Bemaßung auswählen."); return
        value, ok = QInputDialog.getDouble(self, "Bemaßung ändern", f"{dimension['type']} [mm]:", dimension["value"], 0.001, 100000, 4)
        if ok:
            choices = ["(kein Parameter)"] + list(self.document.data.get("parameters", {}))
            current = dimension.get("parameter")
            default_index = choices.index(current) if current in choices else 0
            parameter, parameter_ok = QInputDialog.getItem(self, "Parameterverknüpfung", "Bemaßung mit Parameter verknüpfen:", choices, default_index, False)
            if not parameter_ok: return
            def operation():
                target_parameter = None if parameter == "(kein Parameter)" else parameter
                # Resolve the link before solving. Re-linking after
                # set_dimension() could solve a linked dimension twice
                # and make the transaction appear to do nothing.
                dimension.pop("parameter", None)
                if target_parameter:
                    dimension["parameter"] = target_parameter
                    self.document.set_parameter(target_parameter, value)
                else:
                    self.document.set_dimension(dimension["id"], value)
            try:
                self.mutate("Bemaßung ändern", operation); self.refresh()
            except ValueError as error:
                self.status.setText(str(error))
            else:
                self.status.setText("BemaÃŸung aktualisiert.")

    def delete_parameter(self):
        names = list(self.document.data.get("parameters", {}))
        if not names:
            self.status.setText("Keine Parameter vorhanden.")
            return

        current = self.parameter_table.currentItem() if hasattr(self, "parameter_table") else None
        name = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if name not in names:
            name, ok = QInputDialog.getItem(
                self,
                "Parameter löschen",
                "Parameter auswählen:",
                names,
                0,
                False,
            )
            if not ok:
                return

        try:
            self.mutate("Parameter löschen", lambda: self.document.remove_parameter(name))
            self.refresh()
            self.status.setText(
                f"Parameter {name} gelöscht; verknüpfte Maße bleiben numerisch erhalten."
            )
        except ValueError as error:
            self.status.setText(str(error))

    def parameter_manager(self):
        """Small SolidWorks-like global-variable manager for sketch dimensions."""
        while True:
            names = list(self.document.data.get("parameters", {}))
            choices = ["+ Parameter hinzufügen"] + names + ["Schließen"]
            choice, ok = QInputDialog.getItem(self, "Geometrieparameter", "Parameter auswählen:", choices, 0, False)
            if not ok or choice == "Schließen": return
            if choice == "+ Parameter hinzufügen":
                name, name_ok = QInputDialog.getText(self, "Parameter hinzufügen", "Name (z. B. R_outer):")
                if not name_ok or not name.strip(): continue
                value, value_ok = QInputDialog.getDouble(self, "Parameter hinzufügen", "Wert [mm]:", 1.0, 0.001, 100000, 4)
                if value_ok:
                    try: self.mutate("Parameter hinzufügen", lambda: self.document.add_parameter(name.strip(), value)); self.refresh()
                    except ValueError as error: self.status.setText(str(error))
                continue
            value, value_ok = QInputDialog.getDouble(self, f"Parameter: {choice}", "Neuer Wert [mm]:", self.document.data["parameters"][choice]["value"], 0.001, 100000, 4)
            if value_ok:
                try: self.mutate("Parameter ändern", lambda: self.document.set_parameter(choice, value)); self.refresh()
                except ValueError as error: self.status.setText(str(error))

    def validate_view(self):
        errors = self.document.validate(); self.status.setText("Skizze gültig" if not errors else "\n".join(errors))

    def undo(self): self.history.undo(); self.refresh()

    def redo(self): self.history.redo(); self.refresh()

