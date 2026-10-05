"""Entity-local rear cover coarsening; shared bond stays fine."""
from math import atan2, degrees, hypot

REAR_SIZE_FACTOR = 3.0
TRANSITION_ANGLE_DEG = 20.0


def configure_cover_mesh_size(gmsh, params, volumes):
    def closure(tags):
        result = {(3, tag) for tag in tags}
        level = list(result)
        for _ in range(3):
            level = [(dim, abs(tag)) for dim, tag in gmsh.model.getBoundary(
                level, combined=False, oriented=False)]
            result.update(level)
        return result

    # Contact triangles and their edges must retain the pressure-side resolution.
    rear = (closure(volumes["cover_rear"]) - closure(volumes["cover_front"])
            - closure(volumes["pressure_shell"]))
    old_max = 2.0 if params.mesh_quality == "coarse" else 1.0
    cover = params.cover_shell
    h_front = min(old_max, min(cover.thickness_x_mm, cover.thickness_y_mm) /
                  (3 if params.mesh_quality == "fine" else 2))
    h_rear = h_front * REAR_SIZE_FACTOR
    center = params.initial_gap_mm + params.pressure_shell.radius_mm

    def size(dim, tag, x, y, z, lc):
        if (dim, tag) not in rear:
            return min(lc, old_max)
        theta = degrees(atan2(hypot(y, z), center-x))
        s = min(1., max(0., (theta-cover.free_cap_angle_deg)/TRANSITION_ANGLE_DEG))
        return h_front + s*s*(3-2*s)*(h_rear-h_front)

    gmsh.model.mesh.setSizeCallback(size)
    return max(old_max, h_rear)
