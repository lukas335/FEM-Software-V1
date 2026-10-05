"""Live, undeformed axial geometry preview; never a simulated deformation."""

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Polygon

from model.parameters import SimulationParameters
from model.materials import MATERIALS
from geometry.cover_shell import cover_sphere, cover_regions
from geometry.measurement_shell import measurement_contour


class GeometryPreview(FigureCanvasQTAgg):
    def __init__(self):
        self.figure = Figure(figsize=(6, 5), facecolor="white", layout="constrained")
        super().__init__(self.figure)
        self.ax = self.figure.add_subplot()
        self.setMinimumSize(340, 340)
        self.setToolTip("Unbelasteter Axialschnitt, maßstäblich in mm. Druckausdehnung wird erst vom Solver berechnet.")
        self.parameters = None

    def update_parameters(self, params: SimulationParameters):
        self.parameters = params
        ax = self.ax
        ax.clear()
        ax.set_aspect("equal")
        ax.set_axis_off()
        errors = params.measurement_shell.validate() + params.pressure_shell.validate() + params.cover_shell.validate()
        if errors:
            ax.text(0.5, 0.5, "\n".join(errors), transform=ax.transAxes, ha="center", wrap=True)
            self.draw_idle()
            return
        radius1, radius2 = params.measurement_shell.radius_mm, params.pressure_shell.radius_mm
        gap = params.initial_gap_mm
        height = max(radius1, radius2)
        cover = params.cover_shell
        cover_bounds = None
        overlap = False
        for shell, center, direction, color, name in (
            (params.measurement_shell, -radius1, 1, "#0b9185", "Messschale"),
            (params.pressure_shell, radius2 + gap, -1, "#4285c5", "Druckkugel"),
        ):
            angles = (np.linspace(0, 2 * np.pi, 361) if direction == -1
                      else np.linspace(-np.pi / 2, np.pi / 2, 181))
            outer = shell.radius_mm
            if direction == 1:
                contour = np.asarray(measurement_contour(outer, shell.thickness_x_mm, shell.thickness_y_mm))
                x, y = contour.T
                dimensions = f"R {outer:g} · tx {shell.thickness_x_mm:g} · ty {shell.thickness_y_mm:g} mm"
                ax.annotate("tx", xy=(-shell.thickness_x_mm/2, 0), xytext=(-20, 22),
                            textcoords="offset points", color=color, fontsize=9)
                ax.annotate("", xy=(0, 0), xytext=(-shell.thickness_x_mm, 0),
                            arrowprops=dict(arrowstyle="|-|", color=color))
                ax.annotate("", xy=(center, outer), xytext=(center, outer-shell.thickness_y_mm),
                            arrowprops=dict(arrowstyle="|-|", color=color))
                ax.annotate("ty", xy=(center, outer-shell.thickness_y_mm/2), xytext=(-22, 0),
                            textcoords="offset points", color=color, fontsize=9)
            else:
                inner = outer - shell.thickness_mm
                x = np.r_[center + direction * outer * np.cos(angles),
                          center + direction * inner * np.cos(angles[::-1])]
                y = np.r_[outer * np.sin(angles), inner * np.sin(angles[::-1])]
                dimensions = f"R {outer:g} · t {shell.thickness_mm:g} mm"
            polygon = Polygon(np.column_stack((x, y)), facecolor=color, edgecolor=color, alpha=0.8)
            ax.add_patch(polygon)
            if direction == 1:
                measurement_path = polygon.get_path()
            if direction == -1 and not cover.enabled:
                rear = np.linspace(-np.pi / 2, np.pi / 2, 181)
                rx = np.r_[center + outer * np.cos(rear), center + inner * np.cos(rear[::-1])]
                ry = np.r_[outer * np.sin(rear), inner * np.sin(rear[::-1])]
                ax.add_patch(Polygon(np.column_stack((rx, ry)), facecolor="#94a3b8", edgecolor="#64748b", hatch="///"))
                ax.text(center + outer * 0.55, 0, "fixiert", ha="center", color="#64748b", fontsize=10)
            ax.plot([center, center], [-outer, outer], color=color, linewidth=1, alpha=0.25)
            ax.text(center, outer + 0.12 * height, f"{name}\n{dimensions}",
                    color=color, ha="center", va="bottom", fontsize=10)
        if cover.enabled:
            center = radius2 + gap
            offset, outer_radius = cover_sphere(radius2, cover.thickness_x_mm, cover.thickness_y_mm)
            regions = cover_regions(radius2, cover.thickness_x_mm, cover.thickness_y_mm,
                                    center, cover.free_cap_angle_deg)
            color = "#b56a24"
            all_outer = np.asarray([p for region in regions for p in region["outer"]])
            height = max(height, float(all_outer[:, 1].max()))
            cover_bounds = (min(float(all_outer[:, 0].min()), center + offset),
                            max(float(all_outer[:, 0].max()), center + offset))
            for region, region_color in zip(regions, (color, "#9277b7")):
                for sign in (1, -1):
                    vertices = np.asarray(region["polygon"]) * (1, sign)
                    patch = Polygon(vertices, facecolor=region_color, edgecolor=region_color, alpha=.6)
                    overlap |= patch.get_path().intersects_path(measurement_path, filled=True)
                    ax.add_patch(patch)
                    if region["name"] == "rear":
                        outer = np.asarray(region["outer"]) * (1, sign)
                        ax.plot(*outer.T, color="#334155", linewidth=2, zorder=5)
                        # Short external support strokes; do not hatch the pressure body.
                        for p in outer[::9]:
                            radial = p - (center, 0)
                            radial /= np.linalg.norm(radial)
                            tip = p + height * .035 * (radial + .5 * np.array([-radial[1], radial[0]]))
                            ax.plot([p[0], tip[0]], [p[1], tip[1]], color="#334155", linewidth=.9)
            split = np.deg2rad(cover.free_cap_angle_deg)
            boundary = np.asarray([regions[0]["inner"][-1], regions[0]["outer"][-1]])
            for sign in (1, -1):
                ax.plot(boundary[:, 0], sign * boundary[:, 1], color="#4a354e", linewidth=1.8)
                ax.plot([center, boundary[0, 0]], [0, sign * boundary[0, 1]],
                        color="#64748b", linestyle=":", linewidth=.7)
                arc = np.linspace(0, split, 60)
                ax.plot(center - .35 * radius2 * np.cos(arc), sign * .35 * radius2 * np.sin(arc),
                        color="#64748b", linewidth=.8)
            ax.text(center - .45 * radius2 * np.cos(split / 2), .45 * radius2 * np.sin(split / 2),
                    f"{cover.free_cap_angle_deg:g}° frei", color="#64748b", ha="center", fontsize=9)
            ax.annotate("Deckschale außen\nfest eingespannt", xy=regions[1]["outer"][120],
                        xytext=(.98, .8), textcoords="axes fraction", ha="right", fontsize=9,
                        color="#334155", arrowprops=dict(arrowstyle="->", color="#334155"))
            theta = np.deg2rad(130)
            ax.annotate("Reibungsfreier Kontakt", xy=(center - radius2 * np.cos(theta), -radius2 * np.sin(theta)),
                        xytext=(.98, .13), textcoords="axes fraction", ha="right", fontsize=9,
                        color="#334155", arrowprops=dict(arrowstyle="->", color="#334155"))
            ax.annotate("Materialgrenze · verklebt", xy=boundary.mean(axis=0),
                        xytext=(.02, .83), textcoords="axes fraction", fontsize=9, color="#4a354e",
                        arrowprops=dict(arrowstyle="->", color="#4a354e"))
            ax.plot(center, 0, "o", color="#4285c5", markersize=4)
            ax.plot(center + offset, 0, "x", color=color, markersize=7)
            ax.annotate(f"Außenmittelpunkt\ne = {offset:.3g} mm", xy=(center + offset, 0),
                        xytext=(0, -38), textcoords="offset points", ha="center", color=color,
                        fontsize=9, arrowprops=dict(arrowstyle="-", color=color))
            ax.annotate("", xy=(gap - cover.thickness_x_mm, 0), xytext=(gap, 0),
                        arrowprops=dict(arrowstyle="|-|", color=color))
            ax.annotate(f"tx = {cover.thickness_x_mm:g} mm", xy=(gap - cover.thickness_x_mm / 2, 0),
                        xytext=(-45, 28), textcoords="offset points", ha="right", color=color,
                        fontsize=9, arrowprops=dict(arrowstyle="-", color=color))
            ax.annotate("", xy=(center, -radius2), xytext=(center, -radius2 - cover.thickness_y_mm),
                        arrowprops=dict(arrowstyle="|-|", color=color))
            ax.annotate(f"ty = {cover.thickness_y_mm:g} mm", xy=(center, -radius2 - cover.thickness_y_mm / 2),
                        xytext=(12, 0), textcoords="offset points", color=color, fontsize=9)
            ax.text(.02, .99, f"Deckschale\nVorne: {MATERIALS[cover.front_material_name].name}",
                    transform=ax.transAxes, va="top", color=color, fontsize=10)
            ax.text(.02, .91, f"Hinten: {MATERIALS[cover.rear_material_name].name}",
                    transform=ax.transAxes, va="top", color="#9277b7", fontsize=10)
            if overlap:
                ax.text(.02, .01, "Hinweis: Berührung/Überlappung mit Messschale",
                        transform=ax.transAxes, color="#b42318", fontsize=9, wrap=True)
        ax.axhline(0, color="#cbd5e1", linestyle="--", linewidth=0.8)
        ax.annotate("", xy=(gap, 0), xytext=(0, 0), arrowprops=dict(arrowstyle="|-|", color="#233449"))
        ax.annotate(f"Spalt {gap:g} mm", xy=(gap / 2, 0), xytext=(gap / 2, -height * 0.43),
                    ha="center", color="#233449", arrowprops=dict(arrowstyle="-", color="#94a3b8"))
        ax.annotate(f"P₀ = {params.pressure_mmhg:g} mmHg", xy=(gap + radius2 * 0.12, 0),
                    xytext=(gap + radius2 * 0.65, 0.3 * height), ha="center", color="#4285c5",
                    arrowprops=dict(arrowstyle="->", color="#4285c5"))
        ax.annotate("", xy=(-radius1 * 0.1, -height * 0.85), xytext=(-radius1 * 0.8, -height * 0.85),
                    arrowprops=dict(arrowstyle="->", color="#0b9185", lw=2))
        drive_label = "Weglimit" if params.drive_control == "target_force" else "Hub"
        ax.text(-radius1 * 0.5, -height * 0.98, f"{drive_label} {params.max_displacement_mm:g} mm",
                ha="center", color="#0b9185", fontsize=10)
        left, right = -radius1, gap + 2 * radius2
        if cover_bounds is not None:
            left, right = min(left, cover_bounds[0]), max(right, cover_bounds[1])
        ax.set_xlim(left - height * 0.35, right + height * 0.35)
        ax.set_ylim(-height * 1.2, height * 1.4)
        self.draw_idle()
