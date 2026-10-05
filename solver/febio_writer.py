"""FEBio 4.0 input for the coarse two-shell contact experiment."""

from pathlib import Path
from math import sqrt
from xml.etree import ElementTree as ET

from geometry.mesh_generator import WedgeMesh
from geometry.measurement_shell import measurement_geometry
from model.materials import (
    MATERIALS,
    OgdenMaterial,
    domain_materials,
)
from model.parameters import SimulationParameters
from model.units import mmhg_to_mpa


def _add(parent: ET.Element, tag: str, value: object | None = None, **attributes: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attributes)
    if value is not None:
        element.text = str(value)
    return element


def _material_parameters(name: str) -> tuple[float, float]:
    material = MATERIALS[name]
    if material.model != "neo_hookean":
        raise ValueError(f"Unsupported FEBio material: {material.model}")
    mu, bulk = material.shear_modulus_mpa, material.bulk_modulus_mpa
    if mu <= 0 or bulk <= 0:
        raise ValueError("Material moduli must be positive")
    young = 9 * bulk * mu / (3 * bulk + mu)
    poisson = (3 * bulk - 2 * mu) / (2 * (3 * bulk + mu))
    return young, poisson


def _oriented_triangle(
    face: tuple[int, ...],
    nodes: dict[int, tuple[float, float, float]],
    name: str,
    params: SimulationParameters,
) -> tuple[int, ...]:
    a, b, c = (nodes[n] for n in face)
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )

    if name.startswith("measurement"):
        center = -params.measurement_shell.radius_mm
        if name == "measurement_inner_surface":
            shell = params.measurement_shell
            center = measurement_geometry(
                shell.radius_mm,
                shell.thickness_x_mm,
                shell.thickness_y_mm,
            ).inner_center
        support_sign = -1
    else:
        center = params.initial_gap_mm + params.pressure_shell.radius_mm
        support_sign = 1

    midpoint = tuple((a[i] + b[i] + c[i]) / 3 for i in range(3))

    if name == "measurement_support":
        reference = (support_sign, 0, 0)
    else:
        sign = -1 if name.endswith("_inner_surface") else 1
        reference = (
            sign * (midpoint[0] - center),
            sign * midpoint[1],
            sign * midpoint[2],
        )

    if sum(normal[i] * reference[i] for i in range(3)) < 0:
        return face[0], face[2], face[1]
    return face


def _tangent_axes(points: list[tuple[float, float, float]], center_x: float):
    """Orthonormal tangent basis at a tet centroid on the spherical pressure shell."""
    radial = [sum(point[i] for point in points) / len(points) for i in range(3)]
    radial[0] -= center_x
    length = sqrt(sum(v * v for v in radial))
    n = [v / length for v in radial]
    rho = sqrt(n[1] ** 2 + n[2] ** 2)
    a = (0.0, -n[2] / rho, n[1] / rho) if rho > 1e-12 else (0.0, 1.0, 0.0)
    d = (
        n[1] * a[2] - n[2] * a[1],
        n[2] * a[0] - n[0] * a[2],
        n[0] * a[1] - n[1] * a[0],
    )
    return a, d


def write_febio(mesh: WedgeMesh, params: SimulationParameters, destination: Path, *, retain_visualization=True) -> None:
    """Write a FEBio 4 model with global pressure and two quasi-static steps."""
    if params.validate():
        raise ValueError("Invalid simulation parameters")

    root = ET.Element("febio_spec", version="4.0")
    _add(root, "Module", type="solid")

    materials = _add(root, "Material")
    material_map = domain_materials(params)

    for index, (domain, material) in enumerate(material_map.items(), 1):
        if material.model == "neo_hookean":
            mu, bulk = material.shear_modulus_mpa, material.bulk_modulus_mpa
            young = 9 * bulk * mu / (3 * bulk + mu)
            poisson = (3 * bulk - 2 * mu) / (2 * (3 * bulk + mu))
            entry = _add(materials, "material", id=str(index), name=f"shell_{index}", type="neo-Hookean")
            _add(entry, "E", f"{young:.10g}")
            _add(entry, "v", f"{poisson:.10g}")

        elif material.model == "mooney_rivlin":
            entry = _add(materials, "material", id=str(index), name=f"shell_{index}", type="Mooney-Rivlin")
            for key, value in dict(
                c1=material.c1_mpa,
                c2=material.c2_mpa,
                k=material.bulk_modulus_mpa,
            ).items():
                _add(entry, key, f"{value:.12g}")

        elif material.model == "mooney_rivlin_von_mises" and material.fibers is not None:
            fibers = material.fibers
            entry = _add(
                materials,
                "material",
                id=str(index),
                name=f"shell_{index}",
                type="Mooney-Rivlin von Mises Fibers",
            )
            coefficients = dict(
                c1=material.shear_modulus_mpa / 2,
                c2=0,
                c3=fibers.c3_mpa,
                c4=fibers.c4,
                c5=fibers.c5_mpa,
                lam_max=fibers.lam_max,
                k=material.bulk_modulus_mpa,
                kf=fibers.concentration,
                tp=fibers.angle_deg,
                vmc=1,
                var_n=2,
                gipt=fibers.integration_points,
            )
            for key, value in coefficients.items():
                _add(entry, key, f"{value:.12g}")

        elif material.model == "ogden":
            if not isinstance(material, OgdenMaterial):
                raise TypeError(
                    f"Material '{material.name}' has model='ogden' "
                    "but is not an OgdenMaterial."
                )
            entry = _add(
                materials,
                "material",
                id=str(index),
                name=f"shell_{index}",
                type="Ogden",
            )
            _add(entry, "k", f"{material.bulk_modulus_mpa:.12g}")
            for term_index, term in enumerate(material.terms, start=1):
                _add(entry, f"c{term_index}", f"{term.c_mpa:.12g}")
                _add(entry, f"m{term_index}", f"{term.m:.12g}")

        else:
            raise ValueError(f"Unsupported FEBio material: {material.model}")

    model_mesh = _add(root, "Mesh")
    nodes = _add(model_mesh, "Nodes")
    for node_id, xyz in sorted(mesh.nodes.items()):
        _add(nodes, "node", ",".join(f"{v:.12g}" for v in xyz), id=str(node_id))

    for name, elements in mesh.elements.items():
        group = _add(model_mesh, "Elements", type="tet4", name=name)
        for element_id, connectivity in elements:
            _add(group, "elem", ",".join(map(str, connectivity)), id=str(element_id))

    for name, node_ids in mesh.node_sets.items():
        _add(model_mesh, "NodeSet", ",".join(map(str, node_ids)), name=name)

    support_name = "cover_support" if params.cover_shell.enabled else "pressure_support"
    pressure_support = set(mesh.node_sets[support_name])
    axis_nodes = sorted(
        node
        for node, (_, y, z) in mesh.nodes.items()
        if sqrt(y * y + z * z) < 1e-8 and node not in pressure_support
    )
    if axis_nodes:
        _add(
            model_mesh,
            "NodeSet",
            ",".join(map(str, axis_nodes)),
            name="axis_transverse_fixed",
        )

    for name, triangles in mesh.surfaces.items():
        group = _add(model_mesh, "Surface", name=name)
        for face_index, face in enumerate(triangles, 1):
            oriented = _oriented_triangle(face, mesh.nodes, name, params)
            _add(group, "tri3", ",".join(map(str, oriented)), id=str(face_index))

    cavity = _add(model_mesh, "Surface", name="fluid_cavity")
    for face_index, face in enumerate(mesh.surfaces["pressure_inner_surface"], 1):
        oriented = _oriented_triangle(face, mesh.nodes, "fluid_cavity", params)
        _add(cavity, "tri3", ",".join(map(str, oriented)), id=str(face_index))

    pair = _add(model_mesh, "SurfacePair", name="shell_contact")
    _add(pair, "primary", "measurement_contact")
    _add(
        pair,
        "secondary",
        "cover_outer_contact" if params.cover_shell.enabled else "pressure_contact",
    )

    if params.cover_shell.enabled:
        pair = _add(model_mesh, "SurfacePair", name="pressure_cover_contact")
        _add(pair, "primary", "pressure_contact")
        _add(pair, "secondary", "cover_inner_surface")

    domains = _add(root, "MeshDomains")
    for index, name in enumerate(material_map, 1):
        _add(domains, "SolidDomain", name=name, mat=f"shell_{index}")

    if any(mat.fibers is not None for mat in material_map.values()):
        mesh_data = _add(root, "MeshData")
        for name, material in material_map.items():
            if material.fibers is None:
                continue
            axes = _add(mesh_data, "ElementData", type="mat_axis", elem_set=name)
            center = params.initial_gap_mm + params.pressure_shell.radius_mm
            for lid, (_, connectivity) in enumerate(mesh.elements[name], 1):
                a, d = _tangent_axes([mesh.nodes[node] for node in connectivity], center)
                element = _add(axes, "elem", lid=str(lid))
                _add(element, "a", ",".join(f"{value:.12g}" for value in a))
                _add(element, "d", ",".join(f"{value:.12g}" for value in d))

    boundary = _add(root, "Boundary")
    fixed = _add(boundary, "bc", type="zero displacement", node_set=support_name)
    for axis in ("x", "y", "z"):
        _add(fixed, f"{axis}_dof", 1)

    lateral = _add(boundary, "bc", type="zero displacement", node_set="measurement_support")
    _add(lateral, "y_dof", 1)
    _add(lateral, "z_dof", 1)

    if axis_nodes:
        axis_fixed = _add(
            boundary,
            "bc",
            type="zero displacement",
            node_set="axis_transverse_fixed",
        )
        _add(axis_fixed, "y_dof", 1)
        _add(axis_fixed, "z_dof", 1)

    constraints = _add(root, "Constraints")
    symmetry_parts = ["measurement", "pressure"] + (
        ["cover_front", "cover_rear"] if params.cover_shell.enabled else []
    )
    for shell in symmetry_parts:
        for side in (0, 1):
            constraint = _add(
                constraints,
                "constraint",
                type="symmetry plane",
                surface=f"{shell}_symmetry_{side}",
            )
            _add(constraint, "laugon", 0)
            _add(constraint, "penalty", 10.0)

    contact = _add(root, "Contact")
    for name in (
        ["shell_contact", "pressure_cover_contact"]
        if params.cover_shell.enabled
        else ["shell_contact"]
    ):
        sliding = _add(
            contact,
            "contact",
            type="sliding-elastic",
            surface_pair=name,
        )
        _add(sliding, "auto_penalty", 1)
        _add(sliding, "two_pass", 1)
        _add(sliding, "laugon", 1)
        _add(sliding, "fric_coeff", 0)

    curves = _add(root, "LoadData")
    pressure_curve = _add(curves, "load_controller", id="1", type="loadcurve")
    pressure_points = _add(pressure_curve, "points")
    for time, value in ((0, 0), (1, 1), (2, 1)):
        _add(pressure_points, "point", f"{time},{value}")

    drive_curve = _add(curves, "load_controller", id="2", type="loadcurve")
    drive_points = _add(drive_curve, "points")
    target_mode = params.drive_control == "target_force"
    drive_values = (
        ((0, 0), (1, 0), (1001, 0))
        if target_mode
        else ((0, 0), (1, 0), (2, 1))
    )
    for time, value in drive_values:
        _add(drive_points, "point", f"{time},{value}")

    loads = _add(root, "Loads")
    pressure = _add(
        loads,
        "surface_load",
        type="pressure",
        surface="pressure_inner_surface",
    )
    _add(
        pressure,
        "pressure",
        f"{mmhg_to_mpa(params.pressure_mmhg):.12g}",
        lc="1",
    )
    _add(pressure, "symmetric_stiffness", 0)

    steps = _add(root, "Step")
    for step_index, count in ((1, 10), (2, 20)):
        step = _add(
            steps,
            "step",
            id=str(step_index),
            name="pressure" if step_index == 1 else "indentation",
        )
        control = _add(step, "Control")
        _add(control, "analysis", "STATIC")
        _add(
            control,
            "time_steps",
            20000 if target_mode and step_index == 2 else count,
        )
        _add(control, "step_size", 1 / count)

        solver = _add(control, "solver", type="solid")
        _add(solver, "max_refs", 25)
        _add(solver, "symmetric_stiffness", 0)
        # FEBio 4.13: reject line-search trial states with inverted elements
        # instead of evaluating their residual/stiffness.  This is a numerical
        # safeguard; it does not alter the material or physical constraints.
        _add(solver, "ls_check_jacobians", 1)

        stepper = _add(control, "time_stepper")
        _add(stepper, "dtmin", 0.0001)
        _add(stepper, "dtmax", 1 / count)
        _add(stepper, "max_retries", 8)

        step_bc = _add(step, "Boundary")
        if step_index == 1:
            bc = _add(
                step_bc,
                "bc",
                type="prescribed displacement",
                node_set="measurement_support",
            )
            _add(bc, "dof", "x")
            _add(bc, "value", 0)
        else:
            bc = _add(
                step_bc,
                "bc",
                type="prescribed displacement",
                node_set="measurement_support",
            )
            _add(bc, "dof", "x")
            _add(
                bc,
                "value",
                "1" if target_mode else f"{params.max_displacement_mm:.10g}",
                lc="2",
            )

        if step_index == 2 and not params.constant_pressure:
            step_constraints = _add(step, "Constraints")
            volume = _add(
                step_constraints,
                "constraint",
                type="volume",
                surface="fluid_cavity",
            )
            _add(volume, "laugon", 1)
            _add(volume, "augtol", 0.001)
            _add(
                volume,
                "penalty",
                -0.001 * 3.0 / params.wedge_angle_deg,
            )

    output = _add(root, "Output")
    logfile = _add(output, "logfile", file="solver.log")

    _add(
        logfile,
        "node_data",
        ",".join(map(str, mesh.node_sets["measurement_support"])),
        data="ux;Rx",
        name="support_reaction",
    )
    _add(logfile, "node_data", str(mesh.node_sets["measurement_inner_pole"][0]),
         data="ux", name="measurement_inner_pole")

    if not target_mode:
        _add(
            logfile,
            "face_data",
            data="contact gap;contact pressure",
            name="contact_state",
            surface="measurement_contact",
        )

    if retain_visualization:
        _add(logfile, "node_data", ",".join(map(str, sorted(mesh.nodes))),
             data="ux;uy;uz", name="nodal_displacement")

    if not params.constant_pressure:
        _add(
            logfile,
            "rigid_connector_data",
            str(2 * len(symmetry_parts) + 1),
            data="volume pressure;constrained volume",
            name="cavity_state",
        )

    if retain_visualization:
        plotfile = _add(output, "plotfile", type="febio", file="model.xplt")
        for variable in ("displacement", "Lagrange strain", "contact pressure"):
            _add(plotfile, "var", type=variable)

    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(
        destination,
        encoding="utf-8",
        xml_declaration=True,
    )
