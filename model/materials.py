
from __future__ import annotations

from dataclasses import dataclass, replace, asdict
from math import exp
from typing import Any


# ---------------------------------------------------------------------------
# Fiber model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScleraFibers:
    """
    Illustrative coefficients, NOT fitted human/animal sclera data.

    Units:
        c3_mpa: MPa
        c5_mpa: MPa
        angle_deg: degrees
    """
    c3_mpa: float = 0.05
    c4: float = 30.0
    lam_max: float = 1.1
    concentration: float = 0.0
    angle_deg: float = 0.0
    integration_points: int = 40

    def __post_init__(self) -> None:
        if self.c3_mpa < 0.0:
            raise ValueError("c3_mpa must be non-negative.")

        if self.c4 <= 0.0:
            raise ValueError("c4 must be positive.")

        if self.lam_max <= 1.0:
            raise ValueError("lam_max must be greater than 1.")

        if self.integration_points < 1:
            raise ValueError("integration_points must be >= 1.")

    @property
    def c5_mpa(self) -> float:
        """
        Match the slope of the exponential and linear fiber-stress branches.
        """
        return (
            self.c3_mpa
            * self.c4
            * exp(self.c4 * (self.lam_max - 1.0))
        )


# ---------------------------------------------------------------------------
# Base material definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaterialDefinition:
    """
    Base material definition.

    shear_modulus_mpa:
        Reference / initial shear modulus.

        For simple materials such as neo-Hookean this may be used directly
        by the FEBio writer.

        For more complex models such as Ogden it is primarily a derived
        reference quantity for reporting, validation and stiffness scaling.

    bulk_modulus_mpa:
        Volumetric penalty / bulk modulus used by FEBio.

    source:
        Human-readable provenance of the material parameters.

    notes:
        Additional model limitations or calibration information.
    """
    name: str
    model: str
    shear_modulus_mpa: float
    bulk_modulus_mpa: float
    fibers: ScleraFibers | None = None
    source: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.shear_modulus_mpa < 0.0:
            raise ValueError(
                f"{self.name}: shear_modulus_mpa must be non-negative."
            )

        if self.bulk_modulus_mpa <= 0.0:
            raise ValueError(
                f"{self.name}: bulk_modulus_mpa must be positive."
            )


@dataclass(frozen=True)
class MooneyRivlinMaterial(MaterialDefinition):
    """
    Two-parameter Mooney-Rivlin material.

    For the usual incompressible convention:

        G0 = 2 * (c1 + c2)
    """
    c1_mpa: float = 0.0075
    c2_mpa: float = 0.0025

    def __post_init__(self) -> None:
        super().__post_init__()

        if self.c1_mpa < 0.0:
            raise ValueError(f"{self.name}: c1_mpa must be non-negative.")

        if self.c2_mpa < 0.0:
            raise ValueError(f"{self.name}: c2_mpa must be non-negative.")


# ---------------------------------------------------------------------------
# Ogden material
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OgdenTerm:
    """
    One FEBio Ogden term.

    FEBio convention:

        W_i = c_i / m_i^2 * (...)

    Units:
        c_mpa: MPa
        m: dimensionless
    """
    c_mpa: float
    m: float

    def __post_init__(self) -> None:
        if self.c_mpa < 0.0:
            raise ValueError("Ogden c coefficient must be non-negative.")

        if self.m == 0.0:
            raise ValueError("Ogden exponent m must be non-zero.")


@dataclass(frozen=True)
class OgdenMaterial(MaterialDefinition):
    """
    Multi-term FEBio Ogden material.

    FEBio currently supports up to six terms in the standard Ogden model.

    `shear_modulus_mpa` is retained as a reference / initial small-strain
    stiffness quantity. The actual constitutive response is defined by
    `terms`.
    """
    terms: tuple[OgdenTerm, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()

        if not self.terms:
            raise ValueError(
                f"{self.name}: Ogden material requires at least one term."
            )

        if len(self.terms) > 6:
            raise ValueError(
                f"{self.name}: FEBio Ogden supports at most six terms."
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def abaqus_ogden_to_febio(
    mu_mpa: list[float] | tuple[float, ...],
    alpha: list[float] | tuple[float, ...],
) -> tuple[OgdenTerm, ...]:
    """
    Convert a common Abaqus-style Ogden parameterization to the
    FEBio standard Ogden convention.

    Common source convention:

        W = sum_i 2*mu_i/alpha_i^2 * (...)

    FEBio convention:

        W = sum_i c_i/m_i^2 * (...)

    Therefore:

        c_i = 2 * mu_i
        m_i = alpha_i

    Parameters
    ----------
    mu_mpa:
        Ogden mu coefficients in MPa.

    alpha:
        Ogden exponents.

    Returns
    -------
    tuple[OgdenTerm, ...]
    """
    if len(mu_mpa) != len(alpha):
        raise ValueError(
            "mu_mpa and alpha must have the same number of entries."
        )

    if not mu_mpa:
        raise ValueError("At least one Ogden term is required.")

    return tuple(
        OgdenTerm(
            c_mpa=2.0 * float(mu),
            m=float(a),
        )
        for mu, a in zip(mu_mpa, alpha)
    )


def ogden_reference_shear_modulus(material: OgdenMaterial) -> float:
    """
    Return the reference initial shear modulus used by this project.

    For parameters converted from the common mu-alpha Ogden convention,

        c_i = 2 * mu_i

    so:

        mu_i = c_i / 2

    and therefore:

        G0 ~= sum(mu_i)
           ~= 0.5 * sum(c_i)

    This helper is mainly intended as a consistency / reporting check.
    """
    return 0.5 * sum(term.c_mpa for term in material.terms)


def scaled_material_stiffness(
    material: MaterialDefinition,
    scale: float,
) -> MaterialDefinition:
    """
    Uniformly scale the stress-like stiffness parameters of a material.

    Intended for material uncertainty / sensitivity studies.

    The constitutive shape parameters are kept unchanged.

    For Ogden:
        c_i -> scale * c_i
        m_i unchanged
        bulk modulus -> scale * bulk modulus

    For Mooney-Rivlin:
        c1, c2 -> scale * values
        bulk modulus -> scale * bulk modulus

    For basic neo-Hookean style definitions:
        shear and bulk moduli are scaled directly.

    This is not intended to represent every physically possible material
    variation. It is a convenient project-level stiffness perturbation.
    """
    if scale <= 0.0:
        raise ValueError("Material stiffness scale must be positive.")

    if isinstance(material, OgdenMaterial):
        return replace(
            material,
            shear_modulus_mpa=material.shear_modulus_mpa * scale,
            bulk_modulus_mpa=material.bulk_modulus_mpa * scale,
            terms=tuple(
                replace(term, c_mpa=term.c_mpa * scale)
                for term in material.terms
            ),
        )

    if isinstance(material, MooneyRivlinMaterial):
        return replace(
            material,
            shear_modulus_mpa=material.shear_modulus_mpa * scale,
            bulk_modulus_mpa=material.bulk_modulus_mpa * scale,
            c1_mpa=material.c1_mpa * scale,
            c2_mpa=material.c2_mpa * scale,
        )

    return replace(
        material,
        shear_modulus_mpa=material.shear_modulus_mpa * scale,
        bulk_modulus_mpa=material.bulk_modulus_mpa * scale,
    )


# ---------------------------------------------------------------------------
# Ecoflex 00-30
# ---------------------------------------------------------------------------

# Literature-baseline 2-term Ogden fit.
#
# Source parameterization:
#
#   mu1    = 0.018 MPa
#   alpha1 = 1.000
#
#   mu2    = 0.025 MPa
#   alpha2 = 2.477
#
# Converted to FEBio standard Ogden convention:
#
#   c_i = 2 * mu_i
#   m_i = alpha_i
#
# Therefore:
#
#   c1 = 0.036 MPa
#   m1 = 1.000
#
#   c2 = 0.050 MPa
#   m2 = 2.477
#
# Reference small-strain shear modulus:
#
#   G0 ~= mu1 + mu2 = 0.043 MPa
#
# The chosen bulk modulus of 4.3 MPa corresponds to roughly K/G ~= 100.
# It is a numerical baseline for near-incompressibility, not a measured
# volumetric property of Ecoflex 00-30.
#
# Final quantitative studies should ideally use a fit to tensile data from
# the actual cured material batch.

ECOFLEX_00_30_OGDEN = OgdenMaterial(
    name="Ecoflex 00-30",
    model="ogden",
    shear_modulus_mpa=0.043,
    bulk_modulus_mpa=4.3,
    terms=abaqus_ogden_to_febio(
        mu_mpa=(0.018, 0.025),
        alpha=(1.000, 2.477),
    ),
    source=(
        "Literature baseline; 2-term Ogden parameters converted "
        "to FEBio c/m convention."
    ),
    notes=(
        "Ecoflex 00-30 is Shore 00-30, not Shore A30. "
        "Use as an initial baseline only. Final quantitative simulations "
        "should preferably be calibrated against mechanical test data from "
        "the actual cured batch and processing conditions."
    ),
)


# ---------------------------------------------------------------------------
# Material database
# ---------------------------------------------------------------------------

# Existing prototype / demonstration values are intentionally retained.
#
# Important:
# `silicone_A25` and `silicone_A30` are generic neo-Hookean prototype
# materials. They are NOT substitutes for Ecoflex 00-30.

MATERIALS: dict[str, MaterialDefinition] = {
    "lid_demo": MooneyRivlinMaterial(
        name="Lid-Ersatz (unkalibriert)",
        model="mooney_rivlin",
        shear_modulus_mpa=0.02,
        bulk_modulus_mpa=0.2,
    ),

    "orbital_demo": MooneyRivlinMaterial(
        name="Orbitalgewebe-Ersatz (unkalibriert)",
        model="mooney_rivlin",
        shear_modulus_mpa=0.002,
        bulk_modulus_mpa=0.02,
        c1_mpa=0.00075,
        c2_mpa=0.00025,
    ),

    "silicone_A25": MaterialDefinition(
        name="Silicone A25",
        model="neo_hookean",
        shear_modulus_mpa=0.20,
        bulk_modulus_mpa=2.0,
        source="Prototype / illustrative value",
        notes=(
            "Generic silicone placeholder. "
            "Not a calibrated Ecoflex 00-30 material."
        ),
    ),

    "silicone_A30": MaterialDefinition(
        name="Silicone A30",
        model="neo_hookean",
        shear_modulus_mpa=0.30,
        bulk_modulus_mpa=3.0,
        source="Prototype / illustrative value",
        notes=(
            "Generic silicone placeholder. "
            "Not a calibrated Ecoflex 00-30 material."
        ),
    ),

    "ecoflex_00_30": ECOFLEX_00_30_OGDEN,

    # Structural sclera-like demonstration model.
    # Not a calibrated tissue preset.
    "sclera_demo": MaterialDefinition(
        name="Sklera (unkalibriert)",
        model="mooney_rivlin_von_mises",
        shear_modulus_mpa=0.30,
        bulk_modulus_mpa=30.0,
        fibers=ScleraFibers(),
        source="Illustrative structural model",
        notes=(
            "Not calibrated human or animal sclera data. "
            "K/mu=100; coarse tet4 results require locking and "
            "mesh-convergence assessment."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Material access
# ---------------------------------------------------------------------------

def get_material(name: str) -> MaterialDefinition:
    """
    Retrieve one material with a clear error message.
    """
    try:
        return MATERIALS[name]
    except KeyError as exc:
        available = ", ".join(sorted(MATERIALS))
        raise KeyError(
            f"Unknown material '{name}'. Available materials: {available}"
        ) from exc


def domain_materials(params) -> dict[str, MaterialDefinition]:
    """
    Resolve all domain materials for one simulation parameter set.

    Existing behavior for measurement shell, pressure shell and optional
    cover shell is preserved.
    """
    result: dict[str, MaterialDefinition] = {
        "pressure_shell": get_material(
            params.pressure_shell.material_name
        ),
    }

    if params.measurement_definition is not None:
        from model.measurement_definition import domain_name
        result = {domain_name(region['id']): get_material(region['material'])
                  for region in params.measurement_definition['geometry']['regions']} | result
    else:
        result = {'measurement_shell': get_material(params.measurement_shell.material_name)} | result

    if params.cover_shell.enabled:
        for region in ("front", "rear"):
            material = get_material(
                getattr(
                    params.cover_shell,
                    f"{region}_material_name",
                )
            )

            coefficients = getattr(
                params.cover_shell,
                f"{region}_mr_coefficients",
            )

            if (
                material.model == "mooney_rivlin"
                and coefficients is not None
            ):
                if not isinstance(material, MooneyRivlinMaterial):
                    raise TypeError(
                        f"Cover material '{material.name}' declares "
                        "model='mooney_rivlin' but is not a "
                        "MooneyRivlinMaterial."
                    )

                c1, c2, bulk = coefficients

                material = replace(
                    material,
                    c1_mpa=float(c1),
                    c2_mpa=float(c2),
                    shear_modulus_mpa=2.0 * (float(c1) + float(c2)),
                    bulk_modulus_mpa=float(bulk),
                )

            result[f"cover_{region}"] = material

    return result


# ---------------------------------------------------------------------------
# Reproducibility / snapshots
# ---------------------------------------------------------------------------

def material_snapshot(params) -> dict[str, Any]:
    """
    Return a JSON-serializable material snapshot for reproducibility.

    Preserves the historical two-body snapshot behavior and also includes
    dynamically modified cover materials.

    Ogden term tuples are converted recursively by dataclasses.asdict().
    """
    result: dict[str, Any] = {
        shell.material_name: asdict(
            get_material(shell.material_name)
        )
        for shell in ((params.measurement_shell, params.pressure_shell)
                      if params.measurement_definition is None else (params.pressure_shell,))
    }

    result.update(
        {
            name: asdict(material)
            for name, material in domain_materials(params).items()
            if name.startswith("cover_") or (params.measurement_definition is not None and name.startswith('measurement_'))
        }
    )

    return result


# ---------------------------------------------------------------------------
# Optional validation helpers
# ---------------------------------------------------------------------------

def validate_material_database() -> None:
    """
    Perform inexpensive internal consistency checks.

    Can be called at application startup in debug/development mode.
    """
    for key, material in MATERIALS.items():
        if not key:
            raise ValueError("Material database contains an empty key.")

        if isinstance(material, OgdenMaterial):
            reference_mu = ogden_reference_shear_modulus(material)

            # Do not require exact equality for arbitrary future Ogden
            # materials, but flag obviously inconsistent metadata.
            if material.shear_modulus_mpa > 0.0:
                relative_difference = abs(
                    reference_mu - material.shear_modulus_mpa
                ) / material.shear_modulus_mpa

                if relative_difference > 0.25:
                    raise ValueError(
                        f"{key}: reference shear modulus "
                        f"{material.shear_modulus_mpa:g} MPa is inconsistent "
                        f"with Ogden terms ({reference_mu:g} MPa)."
                    )


# Validate the static built-in database at import time.
# This performs no file I/O and no FEBio calls.
validate_material_database()
