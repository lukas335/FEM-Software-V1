from __future__ import annotations

import math

from preprocessor_common import default_arc_sweep
from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView

class SketchView(QGraphicsView):
    def __init__(self, window, document, history):
        super().__init__(); self.window = window; self.document = document; self.history = history; self.mode = "select"; self.points = []; self.preview_point = None; self.pointer_point = None; self.last_created_ids = []; self.last_line_before = None; self.last_line_history_start = None; self.last_confirmed_line_id = None; self.snap_target = None; self.snap_label = ""; self.inferred_orientation = None; self.dimension_stage = None; self.dimension_data = {}; self.coincident_stage = None; self.coincident_data = {}; self.coincident_extension = False; self.drag_state = None; self.pan_state = None; self.selected_element_ids = set(); self.selected_dimension_ids = set(); self.line_waiting_for_start = False
        self.setScene(QGraphicsScene(self)); self.setMouseTracking(True); self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def set_mode(self, mode):
        self.mode, self.points, self.preview_point, self.pointer_point = mode, [], None, None
        self.inferred_orientation = None
        self.line_waiting_for_start = False
        self.last_line_before = None; self.last_line_history_start = None; self.last_confirmed_line_id = None
        self.dimension_stage, self.dimension_data = None, {}
        self.coincident_stage, self.coincident_data = None, {}
        self.setDragMode(QGraphicsView.RubberBandDrag if mode == "select" else QGraphicsView.NoDrag)
        self.window.status.setText({"line": "Linie: Start- und Endpunkt klicken. Esc beendet.",
            "arc": "Bogen: Mittelpunkt auf x-Achse, Startpunkt, Endpunkt klicken.", "dimension": "Smart Maß: Element oder Punkt auswählen, Maß dynamisch platzieren.",
            "trim": "Trimmen: auf eine Linie oder einen Bogen klicken.",
            "select": "Auswahlmodus: End-/Mittelpunkte oder Elemente ziehen. Strg-Klick = Mehrfachauswahl. Mittlere Maustaste = Pan."}.get(mode, mode))
        if mode == "arc": self.window.status.setText("Bogen: Mittelpunkt, Startpunkt und Endpunkt klicken; x-Achse ist optionaler Snap.")
        if hasattr(self, "window") and hasattr(self.window, "draw"): self.window.draw()

    def set_coincident_mode(self, extension=False):
        self.coincident_extension = extension
        self.set_mode("coincident")
        self.window.status.setText("Coincident: Punkt, Linie/Bogen oder x-/y-Achse wählen; danach zweite Referenz wählen.")

    def _coincident_hover_reference(self, p):
        active_ids = {entity[key] for entity in self.document.elements
                      for key in ('start_point', 'end_point', 'center_point') if key in entity}
        first = self.coincident_data.get('first')
        candidates = [(point, self._screen_distance(p, QPointF(point['x'], point['y'])))
                      for point in self.document.points if point['id'] in active_ids and point['id'] != first]
        if candidates:
            point, distance = min(candidates, key=lambda candidate: candidate[1])
            if distance <= 8.0:
                return point['id']
        axis = self.dimension_axis(p)
        if axis is not None and axis != first:
            return axis
        return self.nearest_element_id(p)

    def _coincident_press(self, p):
        reference = self._coincident_hover_reference(p)
        if reference is None:
            self.window.status.setText("Coincident: bitte eine gültige Referenz wählen.")
            return
        if self.coincident_stage is None:
            self.coincident_stage = "second"; self.coincident_data = {"first": reference}
            self.window.status.setText("Coincident: zweite Referenz wählen.")
            self.window.draw(); return
        first = self.coincident_data.get("first")
        element = self.document.element(first)
        if element and reference in (element.get('start_point'), element.get('end_point'),
                                     element.get('center_point')):
            self.coincident_data = {'first': reference}
            self.window.status.setText('Coincident: Punkt gewählt; jetzt Linie/Bogen, Achse oder zweiten Punkt wählen.')
            self.window.draw(); return
        if first == reference:
            self.window.status.setText("Coincident: zwei unterschiedliche Referenzen wählen.")
            return
        self.coincident_stage, self.coincident_data = None, {}
        self.window.apply_coincident_references([first, reference], self.coincident_extension)

    @staticmethod
    def scene_to_data(point):
        return QPointF(point.x(), -point.y())

    @staticmethod
    def data_to_scene(point):
        return QPointF(point.x(), -point.y())

    def _screen_distance(self, first: QPointF, second: QPointF):
        a = self.mapFromScene(self.data_to_scene(first)); b = self.mapFromScene(self.data_to_scene(second))
        return math.hypot(a.x() - b.x(), a.y() - b.y())

    def snap(self, p: QPointF, allow_axes=True):
        """Snap in screen pixels, so the catchment stays stable while zooming."""
        snap_pixels = 10.0
        candidates = [(QPointF(0, 0), "Ursprung")]
        for point in self.document.points:
            candidate = QPointF(point["x"], point["y"])
            if not any(candidate == existing[0] for existing in candidates):
                candidates.append((candidate, point["id"]))
        nearest = min(candidates, key=lambda item: self._screen_distance(p, item[0]))
        if self._screen_distance(p, nearest[0]) <= snap_pixels:
            self.snap_target, self.snap_label = nearest
            return nearest[0]

        # Endpoint inference: a nearby endpoint can also define a horizontal
        # or vertical snap line.  The perpendicular catchment is deliberately
        # tighter than the direct-point catchment and the longitudinal range
        # is capped, so this does not create an overly aggressive infinite grid.
        cursor_screen = self.mapFromScene(self.data_to_scene(p))
        alignment_candidates = []
        for point in self.document.points:
            anchor = QPointF(point["x"], point["y"])
            anchor_screen = self.mapFromScene(self.data_to_scene(anchor))
            horizontal = QPointF(p.x(), anchor.y())
            vertical = QPointF(anchor.x(), p.y())
            horizontal_perpendicular = abs(cursor_screen.y() - anchor_screen.y())
            horizontal_longitudinal = abs(cursor_screen.x() - anchor_screen.x())
            vertical_perpendicular = abs(cursor_screen.x() - anchor_screen.x())
            vertical_longitudinal = abs(cursor_screen.y() - anchor_screen.y())
            if horizontal_perpendicular <= 8.0 and horizontal_longitudinal <= 72.0:
                alignment_candidates.append((horizontal, f"Horizontal zu {point['id']}"))
            if vertical_perpendicular <= 8.0 and vertical_longitudinal <= 72.0:
                alignment_candidates.append((vertical, f"Vertikal zu {point['id']}"))
        if alignment_candidates:
            aligned = min(alignment_candidates, key=lambda item: self._screen_distance(p, item[0]))
            if self._screen_distance(p, aligned[0]) <= 8.0:
                self.snap_target, self.snap_label = aligned
                return aligned[0]
        if allow_axes:
            x_axis = QPointF(p.x(), 0); y_axis = QPointF(0, p.y())
            if self._screen_distance(p, x_axis) <= snap_pixels:
                self.snap_target, self.snap_label = x_axis, "x-Achse"
                return x_axis
            if self._screen_distance(p, y_axis) <= snap_pixels:
                self.snap_target, self.snap_label = y_axis, "y-Achse"
                return y_axis
        self.snap_target, self.snap_label = None, ""
        return p

    def snap_arc_center(self, raw_p: QPointF):
        """Allow a free arc center while retaining the normal x-axis snap."""
        candidate = self.snap(raw_p, allow_axes=True)
        if candidate.y() < 0.0:
            candidate = QPointF(candidate.x(), 0.0)
        return candidate

    def infer_line_orientation(self, p: QPointF, anchor: QPointF):
        """Apply a small, non-aggressive horizontal/vertical line inference."""
        if self.snap_label.startswith("Horizontal zu"):
            return p, True
        if self.snap_label.startswith("Vertikal zu"):
            return p, False
        if self.snap_label == "x-Achse" and abs(anchor.y()) <= self.model_tolerance():
            return p, True
        if self.snap_label == "y-Achse" and abs(anchor.x()) <= self.model_tolerance():
            return p, False
        dx, dy = p.x() - anchor.x(), p.y() - anchor.y()
        if math.hypot(dx, dy) <= 1e-9:
            return p, None
        # A real endpoint has priority over approximate H/V inference. Never
        # project a caught vertex away from its existing topology.
        if self.snap_target == p and (self.snap_label == 'Ursprung'
                                     or self.document.point(self.snap_label) is not None):
            if abs(dy) <= 1e-6: return p, True
            if abs(dx) <= 1e-6: return p, False
            return p, None
        threshold = math.tan(math.radians(5.0))
        if abs(dy) <= threshold * abs(dx):
            return QPointF(p.x(), anchor.y()), True
        if abs(dx) <= threshold * abs(dy):
            return QPointF(anchor.x(), p.y()), False
        return p, None

    def snap_orthogonal(self, p: QPointF, anchor: QPointF):
        """Apply the SolidWorks-like horizontal/vertical inference first.

        Only then look for a nearby existing point.  This prevents a point
        snap from being silently destroyed by the later axis projection.
        """
        horizontal = abs(p.x() - anchor.x()) >= abs(p.y() - anchor.y())
        constrained = QPointF(p.x(), anchor.y()) if horizontal else QPointF(anchor.x(), p.y())
        return self.snap(constrained, allow_axes=False), horizontal

    def model_tolerance(self):
        return max(0.03, 10.0 / max(abs(self.transform().m11()), 1e-9))

    def nearest_point_id(self, p: QPointF):
        candidates = [(point, self._screen_distance(p, QPointF(point["x"], point["y"])))
                      for point in self.document.points]
        if not candidates: return None
        point, distance = min(candidates, key=lambda item: item[1])
        return point["id"] if distance <= 11.0 else None

    def nearest_element_id(self, p: QPointF):
        candidates = [(element, self.document.distance_to_element(element, p.x(), p.y())) for element in self.document.elements]
        if not candidates: return None
        element, distance = min(candidates, key=lambda item: item[1])
        return element["id"] if distance <= self.model_tolerance() else None

    def nearest_drag_point_id(self, p: QPointF):
        candidates = [(point, self._screen_distance(p, QPointF(point["x"], point["y"]))) for point in self.document.points]
        if not candidates: return None
        point, distance = min(candidates, key=lambda item: item[1])
        return point["id"] if distance <= 11.0 else None

    def select_element(self, element_id):
        self.selected_element_ids = {element_id} if element_id else set()
        self.selected_dimension_ids.clear()
        for item in self.scene().items():
            item_id = item.data(0)
            if self.document.element(item_id): item.setSelected(item_id == element_id)
        self.window.selection_changed()

    def _apply_drag_candidate(self, raw_point):
        state = self.drag_state
        self.document.replace_data(state['before'])
        preferences = {}
        candidate = self.snap(raw_point)
        if state['kind'] == 'point':
            preferences[state['id']] = [candidate.x(), max(0.0, candidate.y())]
        else:
            element = self.document.element(state['id'])
            if element is None:
                raise ValueError('Element für Direktbearbeitung fehlt.')
            dx = candidate.x() - state['start_cursor'].x()
            dy = candidate.y() - state['start_cursor'].y()
            point_ids = {element['start_point'], element['end_point']}
            if element['kind'] == 'arc':
                point_ids.add(element['center_point'])
            for point_id in point_ids:
                point = self.document.point(point_id)
                preferences[point_id] = [point['x'] + dx, max(0.0, point['y'] + dy)]
        self.document.solve_constraints(preferences)

    def _begin_direct_drag(self, raw_point):
        point_id = self.nearest_drag_point_id(raw_point)
        element_id = self.nearest_element_id(raw_point)
        if point_id is None and element_id is None: return False
        if element_id: self.select_element(element_id)
        self.drag_state = {"kind": "point" if point_id else "element",
                           "id": point_id or element_id, "start_cursor": raw_point,
                           "before": self.document.clone_data(), "moved": False, "valid": True}
        return True

    def _update_direct_drag(self, raw_point):
        state = self.drag_state
        state["moved"] = math.hypot(raw_point.x() - state["start_cursor"].x(), raw_point.y() - state["start_cursor"].y()) > self.model_tolerance()
        if not state["moved"]: return
        try:
            self._apply_drag_candidate(raw_point); state["valid"] = True
            self.window.draw(); self.window.status.setText("Direktbearbeitung: gültige parametrische Position")
        except ValueError as error:
            self.document.replace_data(state["before"]); state["valid"] = False
            self.window.draw(); self.window.status.setText(f"Direktbearbeitung blockiert: {error}")

    def _finish_direct_drag(self):
        state = self.drag_state; self.drag_state = None
        if state["moved"] and state["valid"] and self.document.clone_data() != state["before"]:
            self.history.undo_stack.append(("Direktbearbeitung", state["before"], self.document.clone_data())); self.history.redo_stack.clear()
            self.window.refresh(); self.window.status.setText("Direktbearbeitung übernommen.")
        else:
            self.document.replace_data(state["before"]); self.window.draw()

    def dimension_axis(self, p: QPointF):
        candidates = (("x_axis", self._screen_distance(p, QPointF(p.x(), 0.0))),
                      ("y_axis", self._screen_distance(p, QPointF(0.0, p.y()))))
        axis, distance = min(candidates, key=lambda item: item[1])
        return axis if distance <= 12.0 else None

    def dimension_press(self, p: QPointF):
        """SolidWorks-like two-stage dimensioning: choose references, then place."""
        if self.dimension_stage is None:
            axis = self.dimension_axis(p)
            # Use the same hover target that the user sees.  This is
            # important when a point lies close to an axis: the point can
            # be the first reference, while the following click on the
            # axis must still be interpreted as an axis reference.
            if self.snap_label == "x-Achse": axis = "x_axis"
            elif self.snap_label == "y-Achse": axis = "y_axis"
            point_id = self.nearest_point_id(p)
            axis_distance = self._screen_distance(p, QPointF(p.x(), 0.0)) if axis == "x_axis" else (self._screen_distance(p, QPointF(0.0, p.y())) if axis == "y_axis" else math.inf)
            point_distance = math.inf
            if point_id:
                point = self.document.point(point_id)
                point_distance = self._screen_distance(p, QPointF(point["x"], point["y"]))
            element_id = self.nearest_element_id(p)
            point_is_hovered = bool(point_id) and (
                self.snap_label == point_id
                or self.snap_label == "Kreismittelpunkt"
                or point_distance <= axis_distance + 4.0
            )
            if element_id and not point_is_hovered:
                self.dimension_stage = "element_place"
                self.dimension_data = {"element_id": element_id}
                self.preview_point = p
                self.window.status.setText("Element gewählt - Maß mit der Maus platzieren, klicken zum Bestätigen.")
                self.window.draw(); return
            if point_is_hovered:
                self.dimension_stage = "point_reference"
                self.dimension_data = {"point_id": point_id}
                self.window.status.setText("Punkt gewählt - zweiten Punkt oder x-/y-Achse wählen.")
                self.window.draw(); return
            if axis and axis_distance <= 12.0:
                self.dimension_stage = "axis_reference"
                self.dimension_data = {"axis": axis}
                self.window.status.setText("Achse gewÃ¤hlt - Punkt fÃ¼r das MaÃŸ wÃ¤hlen.")
                self.window.draw(); return
            if point_id:
                self.dimension_stage = "point_reference"
                self.dimension_data = {"point_id": point_id}
                self.window.status.setText("Punkt gewählt – zweiten Punkt oder x-/y-Achse wählen.")
                self.window.draw(); return
            if element_id:
                self.dimension_stage = "element_place"
                self.dimension_data = {"element_id": element_id}
                self.preview_point = p
                self.window.status.setText("Element gewählt – Maß mit der Maus platzieren, klicken zum Bestätigen.")
                self.window.draw(); return
            self.window.status.setText("Bitte eine Linie, einen Bogen oder einen vorhandenen Punkt wählen.")
            return
        if self.dimension_stage == "point_reference":
            second_id = self.nearest_point_id(p)
            second_element_id = self.nearest_element_id(p)
            first_point_id = self.dimension_data.get("point_id")
            axis = self.dimension_axis(p)
            if self.snap_label == "x-Achse": axis = "x_axis"
            elif self.snap_label == "y-Achse": axis = "y_axis"
            axis_distance = self._screen_distance(p, QPointF(p.x(), 0.0)) if axis == "x_axis" else (self._screen_distance(p, QPointF(0.0, p.y())) if axis == "y_axis" else math.inf)
            point_distance = math.inf
            if second_id:
                second_point = self.document.point(second_id)
                point_distance = self._screen_distance(p, QPointF(second_point["x"], second_point["y"]))
            axis_priority = bool(axis) and axis_distance <= 12.0
            if axis_priority:
                self.dimension_stage = "point_place"
                self.dimension_data.update({"kind": "point_horizontal" if axis == "y_axis" else "point_vertical", "axis": axis})
                self.preview_point = p; self.window.status.setText("Achse gewÃ¤hlt - MaÃŸ positionieren, klicken zum BestÃ¤tigen."); self.window.draw(); return
            if second_element_id:
                second_element = self.document.element(second_element_id)
                if second_element and first_point_id in (second_element.get("start_point"), second_element.get("end_point"), second_element.get("center_point")):
                    second_element_id = None
            axis = self.dimension_axis(p)
            if self.snap_label == "x-Achse": axis = "x_axis"
            elif self.snap_label == "y-Achse": axis = "y_axis"
            axis_distance = self._screen_distance(p, QPointF(p.x(), 0.0)) if axis == "x_axis" else (self._screen_distance(p, QPointF(0.0, p.y())) if axis == "y_axis" else math.inf)
            point_distance = math.inf
            if second_id:
                second_point = self.document.point(second_id)
                point_distance = self._screen_distance(p, QPointF(second_point["x"], second_point["y"]))
            axis_priority = bool(axis) and axis_distance <= 12.0
            if axis_priority:
                self.dimension_stage = "point_place"
                self.dimension_data.update({"kind": "point_horizontal" if axis == "y_axis" else "point_vertical", "axis": axis})
                self.preview_point = p; self.window.status.setText("Achse gewÃ¤hlt - MaÃŸ positionieren, klicken zum BestÃ¤tigen."); self.window.draw(); return
            if second_id and not axis_priority and second_id != self.dimension_data.get("point_id"):
                first = self.document.point(self.dimension_data["point_id"]); second = self.document.point(second_id)
                horizontal = abs(second["x"] - first["x"]) >= abs(second["y"] - first["y"])
                self.dimension_stage = "point_place"
                self.dimension_data.update({"second_id": second_id, "kind": "horizontal_distance" if horizontal else "vertical_distance"})
                self.preview_point = p; self.window.status.setText("Zweiter Punkt gewählt – Maß positionieren, klicken zum Bestätigen."); self.window.draw(); return
            if second_element_id:
                self.dimension_stage = "pair_place"
                self.dimension_data.update({"first_type": "point", "first_id": self.dimension_data["point_id"], "second_type": "element", "second_id": second_element_id})
                self.preview_point = p; self.window.status.setText("Linie gewÃ¤hlt - Abstand positionieren, klicken zum BestÃ¤tigen."); self.window.draw(); return
            if axis:
                self.dimension_stage = "point_place"
                self.dimension_data.update({"kind": "point_horizontal" if axis == "y_axis" else "point_vertical", "axis": axis})
                self.preview_point = p; self.window.status.setText("Achse gewählt – Maß positionieren, klicken zum Bestätigen."); self.window.draw(); return
            self.window.status.setText("Bitte zweiten Punkt oder eine Achse wählen.")
            return
        if self.dimension_stage == "axis_reference":
            point_id = self.nearest_point_id(p)
            if point_id:
                axis = self.dimension_data.get("axis")
                self.dimension_stage = "point_place"
                self.dimension_data.update({"point_id": point_id, "kind": "point_horizontal" if axis == "y_axis" else "point_vertical"})
                self.preview_point = p; self.window.status.setText("Punkt gewÃ¤hlt - MaÃŸ positionieren, klicken zum BestÃ¤tigen."); self.window.draw(); return
            self.window.status.setText("Bitte einen Punkt als zweite Referenz wÃ¤hlen.")
            return
        if self.dimension_stage == "element_place":
            first_id = self.dimension_data.get("element_id")
            first_element = self.document.element(first_id)
            second_point_id = self.nearest_point_id(p)
            second_element_id = self.nearest_element_id(p)
            if first_element and second_point_id in (first_element.get("start_point"), first_element.get("end_point"), first_element.get("center_point")):
                second_point_id = None
            if second_point_id:
                self.dimension_stage = "pair_place"
                self.dimension_data.update({"first_type": "element", "first_id": first_id, "second_type": "point", "second_id": second_point_id})
                self.preview_point = p; self.window.status.setText("Punkt gewÃ¤hlt - Abstand positionieren, klicken zum BestÃ¤tigen."); self.window.draw(); return
            if second_element_id and second_element_id != first_id:
                self.dimension_stage = "pair_place"
                self.dimension_data.update({"first_type": "element", "first_id": first_id, "second_type": "element", "second_id": second_element_id})
                self.preview_point = p; self.window.status.setText("Zweite Linie gewÃ¤hlt - Abstand positionieren, klicken zum BestÃ¤tigen."); self.window.draw(); return
            self.preview_point = p
            self.window.commit_dynamic_dimension(); return
        if self.dimension_stage in ("point_place", "pair_place"):
            self.preview_point = p
            self.window.commit_dynamic_dimension()

    @staticmethod
    def project_arc_endpoint(center: QPointF, start: QPointF, cursor: QPointF):
        """Keep the live arc endpoint on the radius defined by center/start."""
        dx, dy = cursor.x() - center.x(), cursor.y() - center.y()
        distance = math.hypot(dx, dy)
        radius = math.hypot(start.x() - center.x(), start.y() - center.y())
        if distance <= 1e-9 or radius <= 1e-9: return start
        return QPointF(center.x() + radius * dx / distance, center.y() + radius * dy / distance)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.pan_state = {"position": event.position(),
                              "horizontal": self.horizontalScrollBar().value(),
                              "vertical": self.verticalScrollBar().value()}
            event.accept(); return
        raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
        self.pointer_point = raw_p
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            clicked_item = self.itemAt(event.position().toPoint())
            dimension_id = clicked_item.data(0) if clicked_item is not None and self.document.dimension(clicked_item.data(0)) else None
            if dimension_id:
                if dimension_id in self.selected_dimension_ids: self.selected_dimension_ids.discard(dimension_id)
                else: self.selected_dimension_ids.add(dimension_id)
                for item in self.scene().items():
                    item_id = item.data(0)
                    if self.document.element(item_id) or self.document.dimension(item_id):
                        item.setSelected(item_id in self.selected_element_ids or item_id in self.selected_dimension_ids)
                self.window.selection_changed()
                event.accept()
                return
            element_id = self.nearest_element_id(raw_p)
            matching_items = [item for item in self.scene().items() if item.data(0) == element_id]
            if matching_items:
                selected = element_id in self.selected_element_ids
                if selected: self.selected_element_ids.discard(element_id)
                elif element_id: self.selected_element_ids.add(element_id)
                for item in self.scene().items():
                    item_id = item.data(0)
                    if self.document.element(item_id): item.setSelected(item_id in self.selected_element_ids)
                self.window.selection_changed()
                count = len(self.window.selected_ids())
                self.window.status.setText(f"{count} Geometrieelement(e) ausgewÃ¤hlt | Strg-Klick zum HinzufÃ¼gen/Entfernen")
            event.accept()
            return
        p = raw_p
        if self.mode == "line" and self.line_waiting_for_start and event.button() == Qt.LeftButton:
            self.line_waiting_for_start = False
        if self.mode == "line" and event.button() == Qt.LeftButton:
            self.last_line_before = None; self.last_line_history_start = None; self.last_confirmed_line_id = None
        if self.mode == "dimension":
            self.dimension_press(raw_p); return
        if self.mode == "coincident":
            self._coincident_press(raw_p); return
        if self.mode == "arc" and len(self.points) == 0:
            p = self.snap_arc_center(raw_p)
        elif self.mode == "arc" and len(self.points) == 2:
            candidate = self.snap(raw_p)
            if candidate.y() < 0: candidate = QPointF(candidate.x(), 0.0)
            p = self.project_arc_endpoint(self.points[0], self.points[1], candidate)
        elif self.mode == "line" and self.points:
            p = self.snap(raw_p)
            p, horizontal = self.infer_line_orientation(p, self.points[-1])
            self.inferred_orientation = "horizontal" if horizontal is True else ("vertical" if horizontal is False else None)
        else:
            p = self.snap(raw_p)
        if self.mode == "select":
            if event.button() == Qt.LeftButton:
                clicked_item = self.itemAt(event.position().toPoint())
                dimension_id = clicked_item.data(0) if clicked_item is not None and self.document.dimension(clicked_item.data(0)) else None
                if dimension_id:
                    self.selected_element_ids.clear(); self.selected_dimension_ids = {dimension_id}
                    for item in self.scene().items():
                        item_id = item.data(0)
                        if self.document.element(item_id) or self.document.dimension(item_id):
                            item.setSelected(item_id == dimension_id)
                    self.window.selection_changed()
                    event.accept()
                    return
            if event.button() == Qt.LeftButton and self._begin_direct_drag(raw_p):
                event.accept()
                return
            super().mousePressEvent(event)
            self.selected_element_ids = {item.data(0) for item in self.scene().selectedItems() if self.document.element(item.data(0))}
            self.selected_dimension_ids = {item.data(0) for item in self.scene().selectedItems() if self.document.dimension(item.data(0))}
            self.window.selection_changed()
            return
        if self.mode == "trim":
            element_id = self.nearest_element_id(raw_p)
            if element_id and self.document.element(element_id):
                try:
                    self.window.mutate("Trimmen", lambda: self.document.trim(element_id, raw_p.x(), raw_p.y())); self.window.refresh()
                except ValueError as error: self.window.status.setText(str(error))
            return
        if p.y() < -1e-7:
            self.window.status.setText("Nur die obere Halbschale oberhalb der x-Achse ist erlaubt."); return
        if self.mode == "point":
            holder = []; self.window.mutate("Punkt erstellen", lambda: holder.append(self.document.add_point(p.x(), p.y()))); self.last_created_ids = holder; self.window.refresh(); return
        self.points.append(p)
        if self.mode == "line" and len(self.points) == 2:
            a, b = self.points
            line_before = self.document.clone_data(); line_history_start = len(self.history.undo_stack)
            self.points[-1] = b; a_id = self.nearest_or_new(a); b_id = self.nearest_or_new(b)
            orientation = self.inferred_orientation
            if orientation is None:
                if abs(a.y() - b.y()) <= 1e-7: orientation = "horizontal"
                elif abs(a.x() - b.x()) <= 1e-7: orientation = "vertical"
            holder = []
            def operation():
                line_id = self.document.add_line(a_id, b_id)
                if orientation in ("horizontal", "vertical"):
                    self.document.add_constraint(orientation, [line_id])
                holder.append(line_id)
            self.window.mutate("Linie erstellen", operation); self.last_created_ids = holder; self.last_line_before = line_before; self.last_line_history_start = line_history_start; self.last_confirmed_line_id = holder[0] if holder else None; self.points = [b]; self.preview_point = None; self.inferred_orientation = None; self.window.refresh()
        elif self.mode == "arc" and len(self.points) == 3:
            c, a, b = self.points; center_id = self.nearest_or_new(c); start_id = self.nearest_or_new(a); end_id = self.nearest_or_new(b)
            radius = math.hypot(a.x()-c.x(), a.y()-c.y())
            if radius > 1e-9:
                center = {"x": c.x(), "y": c.y()}; start = {"x": a.x(), "y": a.y()}; end = {"x": b.x(), "y": b.y()}
                sweep = default_arc_sweep(center, start, end)
                holder = []; self.window.mutate("Kreisbogen erstellen", lambda: holder.append(self.document.add_arc(center_id, start_id, end_id, radius, sweep_angle=sweep))); self.last_created_ids = holder
            self.points = []; self.preview_point = None; self.pointer_point = None; self.window.refresh()
        else:
            self.preview_point = p
            if self.mode == "arc":
                phase = "Mittelpunkt gesetzt - nächsten Klick für Startpunkt" if len(self.points) == 1 else "Startpunkt gesetzt - nächsten Klick für Endpunkt"
                self.window.status.setText(phase)
            else:
                self.window.status.setText(f"{len(self.points)} Punkt gesetzt - Maus bewegen, nächster Klick bestätigt")

    def mouseMoveEvent(self, event):
        if self.pan_state is not None:
            start = self.pan_state["position"]; current = event.position()
            self.horizontalScrollBar().setValue(self.pan_state["horizontal"] - round(current.x() - start.x()))
            self.verticalScrollBar().setValue(self.pan_state["vertical"] - round(current.y() - start.y()))
            event.accept(); return
        if self.drag_state is not None:
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            self._update_direct_drag(raw_p)
            event.accept()
            return
        if self.mode == "coincident":
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            self.pointer_point = raw_p
            reference = self._coincident_hover_reference(raw_p)
            if reference in ("x_axis", "y_axis"):
                self.snap_target = QPointF(raw_p.x(), 0.0) if reference == "x_axis" else QPointF(0.0, raw_p.y())
                self.snap_label = "x-Achse" if reference == "x_axis" else "y-Achse"
            elif reference:
                point = self.document.point(reference)
                if point is not None:
                    self.snap_target = QPointF(point["x"], point["y"]); self.snap_label = reference
                else:
                    self.snap_target, self.snap_label = None, reference
            else:
                self.snap_target, self.snap_label = None, ""
            self.window.draw()
            return
        if self.mode == "dimension":
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            self.pointer_point = raw_p
            if self.dimension_stage in (None, "point_reference", "axis_reference"):
                point_id = self.nearest_point_id(raw_p)
                axis = self.dimension_axis(raw_p)
                point_distance = math.inf
                if point_id:
                    point = self.document.point(point_id)
                    point_distance = self._screen_distance(raw_p, QPointF(point["x"], point["y"]))
                axis_distance = math.inf
                if axis == "x_axis": axis_distance = self._screen_distance(raw_p, QPointF(raw_p.x(), 0.0))
                elif axis == "y_axis": axis_distance = self._screen_distance(raw_p, QPointF(0.0, raw_p.y()))
                if point_id and (axis is None or point_distance <= axis_distance + 4.0):
                    point = self.document.point(point_id)
                    self.snap_target = QPointF(point["x"], point["y"])
                    center_ids = {element.get("center_point") for element in self.document.elements
                                  if element.get("kind") == "arc"}
                    self.snap_label = "Kreismittelpunkt" if point_id in center_ids else point_id
                elif axis is not None:
                    self.snap_target = QPointF(raw_p.x(), 0.0) if axis == "x_axis" else QPointF(0.0, raw_p.y())
                    self.snap_label = "x-Achse" if axis == "x_axis" else "y-Achse"
                else:
                    self.snap_target, self.snap_label = None, ""
            else:
                self.snap_target, self.snap_label = None, ""
            self.preview_point = raw_p
            self.window.draw()
            return
        if self.mode == "line" and not self.points:
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            if self.line_waiting_for_start:
                self.preview_point = None; self.pointer_point = None; self.snap_target, self.snap_label = None, ""
                self.window.draw(); self.window.status.setText("Linie pausiert - klicken zum Starten")
                super().mouseMoveEvent(event)
                return
            self.pointer_point = raw_p; self.preview_point = self.snap(raw_p)
            if self.preview_point.y() < -1e-7:
                self.preview_point = None
                self.snap_target, self.snap_label = None, ""
            if self.preview_point is not None and self.preview_point.y() >= -1e-7:
                self.window.draw()
                self.window.status.setText(f"X {self.preview_point.x():.3f} mm Â· Y {self.preview_point.y():.3f} mm Â· Klicken zum Setzen")
            super().mouseMoveEvent(event)
            return
        if self.mode == "arc" and not self.points:
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            self.pointer_point = raw_p
            self.preview_point = self.snap_arc_center(raw_p)
            self.window.draw()
            snap_text = f" · Snap: {self.snap_label}" if self.snap_label else " · Freier Mittelpunkt"
            self.window.status.setText(f"X {self.preview_point.x():.3f} mm · Y {self.preview_point.y():.3f} mm{snap_text} · Klicken zum Setzen")
            super().mouseMoveEvent(event)
            return
        if self.mode == "arc" and not self.points:
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            self.pointer_point = raw_p
            self.preview_point = self.snap_arc_center(raw_p)
            self.window.draw()
            snap_text = f" · Snap: {self.snap_label}" if self.snap_label else " · Mittelpunkt auf x-Achse"
            self.window.status.setText(f"X {self.preview_point.x():.3f} mm · Y 0.000 mm{snap_text} · Klicken zum Setzen")
            super().mouseMoveEvent(event)
            return
        if self.mode in ("line", "arc") and self.points:
            raw_p = self.scene_to_data(self.mapToScene(event.position().toPoint()))
            self.pointer_point = raw_p
            horizontal = None
            if self.mode == "line":
                p = self.snap(raw_p)
                p, horizontal = self.infer_line_orientation(p, self.points[-1])
                self.inferred_orientation = "horizontal" if horizontal is True else ("vertical" if horizontal is False else None)
            else:
                p = self.snap(raw_p)
                if len(self.points) == 2:
                    if p.y() < 0: p = QPointF(p.x(), 0.0)
                    p = self.project_arc_endpoint(self.points[0], self.points[1], p)
            if p.y() >= -1e-7:
                self.preview_point = p; self.window.draw();
                snap_text = f" · Snap: {self.snap_label}" if self.snap_label else (f" · {'Horizontal' if horizontal else 'Vertikal'}" if horizontal is not None else "")
                self.window.status.setText(f"X {p.x():.3f} mm · Y {p.y():.3f} mm{snap_text} · Klicken zum Bestätigen")
        super().mouseMoveEvent(event)

    def nearest_or_new(self, p):
        for point in self.document.points:
            if math.hypot(point["x"]-p.x(), point["y"]-p.y()) < 0.001: return point["id"]
        holder = []; self.window.mutate("Punkt erstellen", lambda: holder.append(self.document.add_point(p.x(), p.y())))
        return holder[0]

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self.pan_state is not None:
            self.pan_state = None; event.accept(); return
        if event.button() == Qt.LeftButton and self.drag_state is not None:
            self._finish_direct_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.mode == "line":
            # The first click of a double-click has already created the
            # line, just like in SolidWorks.  Roll back exactly that
            # creation so the double-click itself confirms nothing.
            line_id = self.last_confirmed_line_id
            if line_id and self.document.element(line_id) and self.last_line_before is not None:
                self.document.replace_data(self.last_line_before)
                if self.last_line_history_start is not None:
                    del self.history.undo_stack[self.last_line_history_start:]
                self.history.redo_stack.clear()
            self.last_line_before = None; self.last_line_history_start = None; self.last_confirmed_line_id = None
            self.points = []; self.preview_point = None; self.pointer_point = None
            self.inferred_orientation = None; self.snap_target, self.snap_label = None, ""
            self.line_waiting_for_start = True
            self.window.status.setText("Linie pausiert - klicken zum Starten")
            self.window.draw()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.mode == "arc":
            self.set_mode("select")
            self.window.status.setText("Zeichenoperation abgebrochen.")
            self.window.draw()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.mode == "select":
            clicked_item = self.itemAt(event.position().toPoint())
            dimension_id = clicked_item.data(0) if clicked_item is not None else None
            if dimension_id and self.document.dimension(dimension_id):
                self.selected_element_ids.clear()
                self.selected_dimension_ids = {dimension_id}
                self.window.selection_changed()
                self.window.edit_dimension()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15; self.scale(factor, factor)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.drag_state is not None:
                self.document.replace_data(self.drag_state["before"]); self.drag_state = None
            self.line_waiting_for_start = False
            self.set_mode("select"); self.window.draw()
        elif event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            step = 100 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 32
            horizontal = 0; vertical = 0
            if event.key() == Qt.Key_Left: horizontal = -step
            elif event.key() == Qt.Key_Right: horizontal = step
            elif event.key() == Qt.Key_Up: vertical = -step
            else: vertical = step
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + horizontal)
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + vertical)
            event.accept()
        elif event.key() == Qt.Key_Delete: self.window.delete_selected()
        else: super().keyPressEvent(event)
