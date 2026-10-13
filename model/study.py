"""Immutable, versioned study definitions and deterministic case generation."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from math import isfinite

from model.parameters import SimulationParameters


@dataclass(frozen=True)
class ReferenceGeometry:
    name: str
    radius_mm: float
    thickness_x_mm: float
    thickness_y_mm: float | None = None

    def __post_init__(self):
        if self.thickness_y_mm is None:
            object.__setattr__(self, "thickness_y_mm", self.thickness_x_mm)

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        if "thickness_mm" in values:
            thickness = values.pop("thickness_mm")
            values.setdefault("thickness_x_mm", thickness)
            values.setdefault("thickness_y_mm", thickness)
        return cls(**values)


@dataclass(frozen=True)
class StudyConfig:
    base_parameters: SimulationParameters
    measurement_radii_mm: tuple[float, ...]
    measurement_thicknesses_mm: tuple[float, ...]
    pressures_mmhg: tuple[float, ...]
    reference_geometries: tuple[ReferenceGeometry, ...]
    fit_start_after_contact_mm: float = 0.2
    fit_end_after_contact_mm: float = 1.0
    minimum_fit_points: int = 5
    pressure_mode: str = "constant"
    fit_mode: str = "automatic"
    # tx is stored in measurement_thicknesses_mm for backward compatibility.
    # ty is optional; None preserves legacy paired tx=ty studies.
    measurement_thicknesses_y_mm: tuple[float, ...] | None = None

    @property
    def measurement_thicknesses_x_mm(self) -> tuple[float, ...]:
        return self.measurement_thicknesses_mm

    @property
    def measurement_thicknesses_y_effective_mm(self) -> tuple[float, ...]:
        return self.measurement_thicknesses_mm if self.measurement_thicknesses_y_mm is None else self.measurement_thicknesses_y_mm

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        # Existing studies retain their original, explicit analysis protocol.
        values.setdefault("fit_mode", "manual")
        values["base_parameters"] = SimulationParameters.from_dict(values["base_parameters"])
        values["reference_geometries"] = tuple(ReferenceGeometry.from_dict(r) for r in values["reference_geometries"])
        if values.get("measurement_thicknesses_y_mm") is not None:
            values["measurement_thicknesses_y_mm"] = tuple(values["measurement_thicknesses_y_mm"])
        for key in ("measurement_radii_mm", "measurement_thicknesses_mm", "pressures_mmhg"):
            values[key] = tuple(values[key])
        config = cls(**values)
        config.cases()  # Validate before accepting a persisted configuration.
        return config

    @property
    def pressures(self):
        return (0.0,) if self.pressure_mode == "none" else tuple(sorted(set(self.pressures_mmhg)))

    def cases(self) -> list[dict]:
        if self.base_parameters.measurement_definition is not None:
            raise ValueError('R/tx/ty-Rasterstudien sind nur für die bisherige Messschale verfügbar. Importierte Geometrieparameter bitte im Editor variieren und als eigene Einzelrechnungen importieren.')
        if self.pressure_mode not in ("closed", "constant", "none"):
            raise ValueError("Unbekannter Druckmodus.")
        if self.fit_mode not in ("automatic", "manual"):
            raise ValueError("Unbekannter Fitmodus.")
        a, b = self.fit_start_after_contact_mm, self.fit_end_after_contact_mm
        if self.fit_mode == "manual" and (not all(isfinite(v) for v in (a, b)) or not 0 <= a < b):
            raise ValueError("Kontaktbezogener Fit: 0 ≤ Beginn < Ende erforderlich.")
        if not isinstance(self.minimum_fit_points, int) or self.minimum_fit_points < 5:
            raise ValueError("Mindestens fünf originale Fitpunkte erforderlich.")
        if not self.measurement_radii_mm or not self.measurement_thicknesses_mm or not self.pressures:
            raise ValueError("Radius-, Dicken- und Drucklisten dürfen nicht leer sein.")
        if self.measurement_thicknesses_y_mm is not None and not self.measurement_thicknesses_y_mm:
            raise ValueError("ty-Liste darf nicht leer sein.")
        ny = 1 if self.measurement_thicknesses_y_mm is None else len(self.measurement_thicknesses_y_mm)
        if len(self.measurement_radii_mm) * len(self.measurement_thicknesses_mm) * ny * len(self.pressures) > 10000:
            raise ValueError("Maximal 10.000 Rasterfälle pro Studie.")
        errors = self.base_parameters.validate()
        if errors:
            raise ValueError(" ".join(errors))
        geometries = [(r, tx, ty, None) for r in self.measurement_radii_mm
                      for tx in self.measurement_thicknesses_mm
                      for ty in ((tx,) if self.measurement_thicknesses_y_mm is None else self.measurement_thicknesses_y_mm)]
        geometries += [(r.radius_mm, r.thickness_x_mm, r.thickness_y_mm, r.name.strip()) for r in self.reference_geometries]
        if any(not r.name.strip() for r in self.reference_geometries):
            raise ValueError("Referenzen benötigen einen Namen.")
        unique = {}
        for radius, tx, ty, reference in geometries:
            for pressure in self.pressures:
                params = replace(self.base_parameters,
                    measurement_shell=replace(self.base_parameters.measurement_shell,
                                              radius_mm=radius, thickness_x_mm=tx, thickness_y_mm=ty),
                    pressure_mmhg=pressure, constant_pressure=self.pressure_mode != "closed")
                if errors := params.validate():
                    raise ValueError(f"R={radius}, tx={tx}, ty={ty}, P={pressure}: " + " ".join(errors))
                identity = asdict(params)
                if identity.get('measurement_definition') is None:
                    identity.pop('measurement_definition', None)  # Preserve legacy case IDs.
                shell_identity = identity["measurement_shell"]
                if tx == ty:
                    shell_identity["thickness_mm"] = shell_identity.pop("thickness_x_mm")
                    shell_identity.pop("thickness_y_mm")
                if not params.cover_shell.enabled:
                    identity.pop("cover_shell")  # Preserve historical two-body case IDs.
                if params.drive_control == "displacement":
                    # Preserve IDs of existing displacement studies. Inactive
                    # force-controller settings do not change the physics.
                    for field in ("drive_control", "target_force_n", "force_tolerance_abs_n", "force_tolerance_rel",
                                  "approach_step_mm", "min_drive_step_mm", "max_drive_step_mm", "max_control_trials"):
                        identity.pop(field)
                encoded = json.dumps(identity, sort_keys=True, allow_nan=False)
                key = sha256(encoded.encode()).hexdigest()[:16]
                case = unique.setdefault(key, {"id": key, "parameters": asdict(params),
                    "references": [], "in_grid": False, "status": "pending", "attempts": [],
                    "result": None, "error": ""})
                if reference is None:
                    case["in_grid"] = True
                elif reference not in case["references"]:
                    case["references"].append(reference)
        return list(unique.values())
