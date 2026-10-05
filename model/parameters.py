from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from model.materials import MATERIALS

MeshQuality = Literal["coarse", "normal", "fine"]


@dataclass(frozen=True)
class ShellParameters:
    radius_mm: float
    thickness_mm: float
    material_name: str

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not isfinite(self.radius_mm) or self.radius_mm <= 0:
            errors.append("Der Radius muss größer als 0 sein.")
        if not isfinite(self.thickness_mm) or self.thickness_mm <= 0 or self.thickness_mm >= self.radius_mm:
            errors.append("Die Wanddicke muss zwischen 0 und dem Radius liegen.")
        if self.material_name not in MATERIALS:
            errors.append("Ein bekanntes Material muss gewählt werden.")
        return errors


@dataclass(frozen=True)
class MeasurementShellParameters:
    radius_mm: float
    thickness_x_mm: float
    thickness_y_mm: float
    material_name: str

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        if "thickness_mm" in values:
            thickness = values.pop("thickness_mm")
            for key in ("thickness_x_mm", "thickness_y_mm"):
                if key in values and values[key] != thickness:
                    raise ValueError("Widersprüchliche Messschalen-Dicken im Snapshot.")
                values[key] = thickness
        return cls(**values)

    def validate(self):
        errors = ShellParameters(self.radius_mm, self.thickness_x_mm, self.material_name).validate()
        if not isfinite(self.thickness_y_mm) or not 0 < self.thickness_y_mm < self.radius_mm:
            errors.append("Messschale: ty muss zwischen 0 und Außenradius liegen.")
        return errors

    def concentric_thickness(self):
        if self.thickness_x_mm != self.thickness_y_mm:
            raise ValueError("Parameterstudien benötigen derzeit noch tx = ty; exzentrische Einzelrechnungen sind möglich.")
        return self.thickness_x_mm


@dataclass(frozen=True)
class CoverShellParameters:
    enabled: bool = False
    thickness_x_mm: float = 1.0
    thickness_y_mm: float = 1.0
    front_material_name: str = "silicone_A30"
    rear_material_name: str = "silicone_A30"
    free_cap_angle_deg: float = 70.0
    front_mr_coefficients: tuple[float, float, float] | None = None
    rear_mr_coefficients: tuple[float, float, float] | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "CoverShellParameters":
        values = dict(data)
        legacy_material = values.pop("material_name", "silicone_A30")
        values.setdefault("front_material_name", legacy_material)
        values.setdefault("rear_material_name", legacy_material)
        for name in ("front_mr_coefficients", "rear_mr_coefficients"):
            if values.get(name) is not None:
                values[name] = tuple(values[name])
        return cls(**values)

    def validate(self) -> list[str]:
        if not isinstance(self.enabled, bool):
            return ["Deckhalbschale: Aktivierung muss boolesch sein."]
        if not self.enabled:
            return []
        errors = []
        for coefficients in (self.front_mr_coefficients, self.rear_mr_coefficients):
            if coefficients is not None and (len(coefficients) != 3 or
                    not all(isfinite(v) and v >= 0 for v in coefficients) or
                    coefficients[0] + coefficients[1] <= 0 or coefficients[2] <= 0):
                errors.append("Deckschale: MR benötigt c1,c2 ≥ 0, c1+c2 > 0 und K > 0 (MPa).")
        if not all(isfinite(v) and v > 0 for v in (self.thickness_x_mm, self.thickness_y_mm)):
            errors.append("Deckhalbschale: tx und ty müssen endlich und positiv sein.")
        if self.front_material_name not in MATERIALS or self.rear_material_name not in MATERIALS:
            errors.append("Deckhalbschale: bekanntes Material auswählen.")
        if not isfinite(self.free_cap_angle_deg) or not 0 < self.free_cap_angle_deg < 90:
            errors.append("Freier Kappenwinkel muss zwischen 0° und 90° liegen.")
        return errors


@dataclass(frozen=True)
class SimulationParameters:
    measurement_shell: MeasurementShellParameters
    pressure_shell: ShellParameters
    pressure_mmhg: float = 15.0
    max_displacement_mm: float = 1.0
    fit_start_mm: float = 0.2
    fit_end_mm: float = 0.8
    wedge_angle_deg: float = 3.0
    mesh_quality: MeshQuality = "normal"
    initial_gap_mm: float = 1.0
    constant_pressure: bool = False
    drive_control: Literal["displacement", "target_force"] = "displacement"
    target_force_n: float = 0.2
    force_tolerance_abs_n: float = 0.02
    force_tolerance_rel: float = 0.0
    approach_step_mm: float = 0.05
    min_drive_step_mm: float = 1e-4
    max_drive_step_mm: float = 0.1
    max_control_trials: int = 200
    cover_shell: CoverShellParameters = field(default_factory=CoverShellParameters)

    def __post_init__(self):
        # Compatibility for older Python callers as well as saved JSON.
        if isinstance(self.measurement_shell, ShellParameters):
            shell = self.measurement_shell
            object.__setattr__(self, "measurement_shell", MeasurementShellParameters(
                shell.radius_mm, shell.thickness_mm, shell.thickness_mm, shell.material_name))

    @property
    def force_tolerance_n(self) -> float:
        return max(self.force_tolerance_abs_n, self.force_tolerance_rel * self.target_force_n)

    @classmethod
    def from_dict(cls, data: dict) -> "SimulationParameters":
        """Restore a GUI/CLI parameter snapshot, without silently dropping fields."""
        values = dict(data)
        values["cover_shell"] = CoverShellParameters.from_dict(values.get("cover_shell", {}))
        values["measurement_shell"] = MeasurementShellParameters.from_dict(values["measurement_shell"])
        values["pressure_shell"] = ShellParameters(**values["pressure_shell"])
        result = cls(**values)
        if errors := result.validate():
            raise ValueError(" ".join(errors))
        return result

    def validate(self) -> list[str]:
        errors = self.measurement_shell.validate() + self.pressure_shell.validate()
        errors.extend(self.cover_shell.validate())
        if self.cover_shell.enabled and self.initial_gap_mm <= self.cover_shell.thickness_x_mm:
            errors.append("Anfangsspalt muss größer als Deckschalen-Pol-Dicke tx sein (plus Druckausdehnung).")
        if self.drive_control not in ("displacement", "target_force"):
            errors.append("Unbekannte Zustellsteuerung.")
        force_values = (self.target_force_n, self.force_tolerance_abs_n,
                        self.approach_step_mm, self.min_drive_step_mm, self.max_drive_step_mm)
        if not all(isfinite(v) and v > 0 for v in force_values):
            errors.append("Zielkraft, Toleranzen und Wegschritte müssen endlich und positiv sein.")
        if not isfinite(self.force_tolerance_rel) or not 0 <= self.force_tolerance_rel < 1:
            errors.append("Relative Krafttoleranz muss zwischen 0 (aus) und kleiner als 1 liegen.")
        if not self.min_drive_step_mm <= self.approach_step_mm <= self.max_drive_step_mm:
            errors.append("Wegschritte: Minimum ≤ Annäherung ≤ Maximum erforderlich.")
        if type(self.max_control_trials) is not int or not 1 <= self.max_control_trials <= 10000:
            errors.append("Die maximale Versuchszahl muss zwischen 1 und 10.000 liegen.")
        if not isinstance(self.constant_pressure, bool):
            errors.append("Konstanter Druck muss ein boolescher Wert sein.")
        if self.measurement_shell.material_name == "sclera_demo":
            errors.append("Das Sklera-Modell ist derzeit nur für die Druckschale vorgesehen.")
        if not isfinite(self.initial_gap_mm) or self.initial_gap_mm < 0:
            errors.append("Der Anfangsspalt muss endlich und nicht negativ sein.")
        if not isfinite(self.pressure_mmhg) or self.pressure_mmhg < 0:
            errors.append("Der Druck darf nicht negativ sein.")
        if not isfinite(self.max_displacement_mm) or self.max_displacement_mm <= 0:
            errors.append("Die maximale Verschiebung muss größer als 0 sein.")
        if not all(isfinite(v) for v in (self.fit_start_mm, self.fit_end_mm)) or not 0 <= self.fit_start_mm < self.fit_end_mm <= self.max_displacement_mm:
            errors.append("Der Fit-Bereich muss innerhalb der Verschiebung liegen.")
        if not 0 < self.wedge_angle_deg < 180:
            errors.append("Der Wedge-Winkel muss zwischen 0 und 180 Grad liegen.")
        if self.mesh_quality not in ("coarse", "normal", "fine"):
            errors.append("Ungültige Netzqualität.")
        return errors
