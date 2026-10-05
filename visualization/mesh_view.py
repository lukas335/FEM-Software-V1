"""Animated axial mesh using actual FEBio nodal displacements."""

from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from model.deformation import MeshTopology, DeformationHistory, read_deformation, read_mesh


class MeshView(FigureCanvasQTAgg):
    def __init__(self):
        super().__init__(Figure(figsize=(10, 6), facecolor="white", layout="constrained"))
        self.ax, self.detail_ax = self.figure.subplots(1, 2, gridspec_kw={"width_ratios": [1, 1.1]})
        self.mesh: MeshTopology | None = None
        self.history: DeformationHistory | None = None
        self.scale = 1.0
        self.time = 0.0
        self.reference_visible = True
        self.artists = []
        self.reference_artists = []
        self.fixed_indices = np.array([], dtype=int)
        self.message = ""
        self.show_empty("Kein Ergebnis geladen")

    def show_empty(self, message: str):
        self.mesh = None
        self.history = None
        self.message = message
        for ax in (self.ax, self.detail_ax):
            ax.clear()
            ax.set_axis_off()
        self.ax.text(0.5, 0.5, message, ha="center", va="center",
                     transform=self.ax.transAxes, color="#64748b", fontsize=10, wrap=True)
        self.draw_idle()

    def load_mesh(self, path: Path):
        try:
            mesh = read_mesh(path)
        except (OSError, ET.ParseError, ValueError, KeyError, TypeError) as exc:
            self.show_empty(f"Netz nicht verfügbar.\n{exc}")
            return
        self.mesh = mesh
        root = ET.parse(path)
        support = root.find("./Mesh/NodeSet[@name='cover_support']")
        if support is None:
            support = root.find("./Mesh/NodeSet[@name='pressure_support']")
        fixed_ids = set(map(int, support.text.split(","))) if support is not None and support.text else set()
        self.fixed_indices = np.flatnonzero(np.isin(mesh.node_ids, list(fixed_ids)))
        self.history = None
        self.time = 0.0
        try:
            self.history = read_deformation(path.with_name("nodal_displacements.csv"), mesh)
            self.message = f"{len(self.history.times)} Zustände inkl. Referenz · tatsächliche Knotenverschiebungen"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.message = ("Nur Referenznetz: Verschiebungsexport fehlt." if isinstance(exc, FileNotFoundError)
                            else f"Animation nicht verfügbar: {exc}")
        self._build_artists()
        self.reset_view()
        self.set_time(0)

    @staticmethod
    def _project(coordinates: np.ndarray) -> np.ndarray:
        # Axisymmetric axial section: project onto x/r, then mirror radially.
        return np.column_stack((coordinates[:, 0], np.hypot(coordinates[:, 1], coordinates[:, 2])))

    @staticmethod
    def _mirrored(points: np.ndarray, connectivity: np.ndarray) -> np.ndarray:
        upper = points[connectivity]
        lower = upper * np.array([1.0, -1.0])
        return np.concatenate((upper, lower))

    def _build_artists(self):
        self.artists.clear()
        self.reference_artists.clear()
        reference = self._project(self.mesh.coordinates)
        colors = ("#0b9687", "#4f70cf", "#e5a12e", "#a66bba")
        names = ("Messschale", "Druckkörper", "Deckschale vorne", "Deckschale hinten")
        for ax, title in ((self.ax, "Gesamtmodell"), (self.detail_ax, "Kontaktzone · Detail")):
            ax.clear()
            ax.set_axis_on()
            ax.set_facecolor("#fafcfd")
            ax.set_title(title, color="#263c52", fontsize=11, loc="left", pad=16)
            ax.set_aspect("equal", adjustable="box")
            ax.axhline(0, color="#9bafbf", linestyle=(0, (4, 4)), linewidth=0.7)
            ax.grid(color="#edf2f6", linewidth=0.6)
            ax.set_axisbelow(True)
            ax.set_xlabel("x [mm]", color="#64748b", fontsize=9)
            ax.set_ylabel("Radialkoordinate [mm]", color="#64748b", fontsize=9)
            ax.tick_params(colors="#64748b", labelsize=8, length=0)
            for spine in ax.spines.values():
                spine.set_visible(False)
            for part, color, name in zip(self.mesh.parts, colors, names):
                ghost = LineCollection(self._mirrored(reference, part.edges), colors="#94a3b8",
                                       linewidths=0.5, alpha=0.35, linestyle="dashed")
                ghost.set_visible(self.reference_visible)
                ax.add_collection(ghost)
                self.reference_artists.append(ghost)
                fill = PolyCollection(self._mirrored(reference, part.triangles),
                                      facecolors=color, edgecolors="none", alpha=0.13)
                edges = LineCollection(self._mirrored(reference, part.edges), colors=color,
                                       linewidths=0.8, alpha=0.95, label=name)
                ax.add_collection(fill)
                ax.add_collection(edges)
                self.artists.append((part, fill, edges))
                fixed_edges = part.edges[np.isin(part.edges, self.fixed_indices).all(axis=1)]
                if len(fixed_edges):
                    ax.add_collection(LineCollection(self._mirrored(reference, fixed_edges),
                                                     colors="#64748b", linewidths=1.0,
                                                     label="Fest eingespannt", zorder=4))
        self.ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), frameon=False, fontsize=8, ncol=3)

    def reset_view(self):
        if self.mesh is None:
            return
        coordinates = self.mesh.coordinates
        positions = coordinates[None, ...]
        if self.history is not None:
            positions = coordinates[None, ...] + self.scale * self.history.displacements
        xlo, xhi = positions[:, :, 0].min(), positions[:, :, 0].max()
        radius = np.hypot(positions[:, :, 1], positions[:, :, 2]).max()
        self.ax.set_xlim(xlo - 2, xhi + 2)
        self.ax.set_ylim(-radius * 1.12, radius * 1.12)
        measurement = coordinates[np.unique(self.mesh.parts[0].triangles)]
        contact_part = next((part for part in self.mesh.parts if part.name == "cover_front"), self.mesh.parts[1])
        pressure = coordinates[np.unique(contact_part.triangles)]
        center = (measurement[:, 0].max() + pressure[:, 0].min()) / 2
        half_height = max(1.0, measurement[:, 1].max() * 0.75)
        self.detail_ax.set_xlim(center - half_height * 0.6, center + half_height * 0.6)
        self.detail_ax.set_ylim(-half_height, half_height)
        self.draw_idle()

    def set_scale(self, scale: float):
        self.scale = scale
        if self.mesh is not None:
            self.reset_view()
            self.set_time(self.time)

    def set_reference_visible(self, visible: bool):
        self.reference_visible = visible
        for artist in self.reference_artists:
            artist.set_visible(visible)
        self.draw_idle()

    def set_time(self, time: float) -> float:
        if self.mesh is None:
            return 0.0
        self.time = time
        displacement = (self.history.at_time(time) if self.history is not None
                        else np.zeros_like(self.mesh.coordinates))
        projection = self._project(self.mesh.coordinates + self.scale * displacement)
        for part, fill, edges in self.artists:
            fill.set_verts(self._mirrored(projection, part.triangles))
            edges.set_segments(self._mirrored(projection, part.edges))
        self.draw_idle()
        return float(np.linalg.norm(displacement, axis=1).max())
