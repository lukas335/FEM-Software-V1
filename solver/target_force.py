"""Adaptive force-target workflow and accepted-only exports."""

import csv
import json
import subprocess

from solver.accepted_history import merge_accepted
from solver.febio_session import FEBioSession, TrialFailed
from solver.force_controller import ForceController


def run_target_force(directory, params, executable, metadata, timeout=900, *, retain_visualization=True,
                     final_only_visualization=False):
    from solver.prototype import parse_reaction_log, export_displacements, export_cavity, trim_displacement_history

    session = FEBioSession(directory, params, executable, timeout, retain_visualization=retain_visualization)
    metadata.update(drive_control="target_force", target_force_n=params.target_force_n,
                    force_tolerance_n=params.force_tolerance_n, target_reached=False)

    def save():
        (directory / "case.json").write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8")

    def save_path(controller):
        path = [session.entries[s.checkpoint_id] for s in controller.accepted]
        (directory / "accepted_path.json").write_text(json.dumps(path, indent=2), encoding="utf-8")
        return path

    print("STATUS: Druckaufbau und Restart-Checkpoint …", flush=True)
    try:
        initial = session.initialize()
    except (TrialFailed, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        metadata.update(febio_status="solver_failed", termination_reason=(
            "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "initialization_failed"), error=str(exc))
        save()
        raise RuntimeError(f"Zielkraft-Initialisierung fehlgeschlagen: {exc}") from exc
    controller = ForceController(params, initial)
    metadata["preload_minimum_axial_gap_mm"] = session.preload_gap_mm
    save_path(controller)
    fields = ("trial", "parent", "target_drive_mm", "actual_drive_mm", "force_n", "solver_time",
              "converged", "accepted", "reason", "error")
    with (directory / "control_history.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        while request := controller.propose_trial():
            row = dict(trial=controller.trials + 1, parent=request.source_checkpoint_id,
                       target_drive_mm=request.target_drive_mm, converged=False, accepted=False)
            print(f"STATUS: Zielkraft {params.target_force_n:g} N · Versuch {controller.trials + 1} · "
                  f"u = {request.target_drive_mm:.4f} mm · F = {controller.current.force_n:.5f} N", flush=True)
            try:
                state = session.advance(request.source_checkpoint_id, request.target_drive_mm)
                accepted, reason = controller.observe_converged(state)
                row.update(converged=True, accepted=accepted, reason=reason,
                           actual_drive_mm=state.drive_mm, force_n=state.force_n, solver_time=state.solver_time)
            except subprocess.TimeoutExpired as exc:
                controller.reason = "timeout"
                row.update(reason="timeout", error=str(exc))
            except TrialFailed as exc:
                row.update(reason=controller.observe_failed(), error=str(exc))
            except (ValueError, OSError) as exc:
                controller.reason = "solver_failed"
                row.update(reason="solver_failed", error=str(exc))
            writer.writerow(row)
            stream.flush()
            save_path(controller)
    state = controller.current
    metadata.update(termination_reason=controller.reason, target_reached=controller.reason == "target_reached",
                    achieved_force_n=state.force_n, achieved_drive_mm=state.drive_mm,
                    force_error_n=state.force_n - params.target_force_n, control_trials=session.counter,
                    accepted_steps=len(controller.accepted) - 1, accepted_end_time=state.solver_time)
    if len(controller.accepted) < 2:
        metadata["febio_status"] = "solver_failed"
        save()
        raise RuntimeError(f"Keine gültige Zustellung: {controller.reason}")
    try:
        path = save_path(controller)
        log_path = merge_accepted(directory, path)
        input_file = directory / "model.feb"
        history = parse_reaction_log(log_path, session.support, params.wedge_angle_deg, session.measurement_pole)
        # Use only the converged endpoint of each accepted restart branch.
        # Intermediate records are branch-start states and can carry tiny
        # serialization differences in relative indentation.
        endpoint_times = [float(item["solver_time"]) for item in path]
        endpoints = []
        for item in history:
            if any(abs(item[0] - endpoint) <= 1e-7 for endpoint in endpoint_times):
                endpoints.append(item)
        if len(endpoints) != len(endpoint_times):
            raise ValueError("Accepted endpoint missing from relative indentation history")
        unique = []
        for item in endpoints:
            if unique and abs(item[1] - unique[-1][1]) < 1e-10:
                if item[1] > 1e-10:
                    unique[-1] = item
            else:
                unique.append(item)
        if retain_visualization:
            metadata["deformation_frames"] = export_displacements(log_path, input_file, session.nodes, end_time=state.solver_time)
            metadata["cavity_results"] = export_cavity(log_path, input_file, params)
            if final_only_visualization:
                trim_displacement_history(directory / "nodal_displacements.csv")
        with (directory / "force_displacement.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("time", "drive_mm", "reaction_full_N"))
            writer.writerows(unique)
    except (ValueError, OSError) as exc:
        metadata.update(febio_status="result_parse_failed", error=str(exc))
        save()
        raise
    metadata["febio_status"] = "success" if metadata["target_reached"] else "partial"
    metadata["log_files"] = ["solver.log"]
    from solver.cleanup import finalize_successful_run
    finalize_successful_run(directory, metadata)
    print(f"STATUS: {'Zielkraft erreicht' if metadata['target_reached'] else 'Zielkraft NICHT erreicht: ' + controller.reason}"
          f" · F = {state.force_n:.5f} N · u = {state.drive_mm:.4f} mm", flush=True)
    return 0
