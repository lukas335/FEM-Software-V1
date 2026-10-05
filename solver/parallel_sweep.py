"""Parallel execution of independent FEBio parameter-study cases.

Drop this file into ``solver/parallel_sweep.py``.

The module deliberately does not modify the FEM solver implementation.  Each
case is executed through the existing ``python -m solver.prototype`` command,
so mesh generation, FEBio input generation, target-force control, restart
handling, result parsing, and case metadata remain exactly where they are now.

Typical use::

    from solver.parallel_sweep import SweepCase, run_parallel_sweep

    cases = [
        SweepCase("r8_t1_p10", params_1),
        SweepCase("r8_t1_p15", params_2),
        SweepCase("r8_t1_p20", params_3),
    ]

    results = run_parallel_sweep(
        cases,
        febio_path=r"C:\\Program Files\\FEBioStudio2\\bin\\febio4.exe",
        output_root="work/simulations/sweep_001",
        max_parallel_jobs=4,
    )

The caller should run ``run_parallel_sweep`` outside the Qt main thread.  A
QThread/worker can call it directly.  ``progress_callback`` is called by the
coordinator thread, never by the individual executor workers.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable, Iterable, Sequence

from model.parameters import SimulationParameters


ProgressCallback = Callable[["SweepResult", int, int], None]


@dataclass(frozen=True)
class SweepCase:
    """One independent FEM case in a parameter study.

    ``name`` is only a human-readable label.  A short hash of the complete
    parameter snapshot is appended to the run-directory name, preventing
    accidental collisions between cases with the same label.
    """

    name: str
    parameters: SimulationParameters


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one FEBio parameter-study case."""

    name: str
    case_id: str
    run_directory: Path
    return_code: int | None
    status: str
    elapsed_s: float
    error: str | None = None
    cached: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status in {"success", "partial"}


@dataclass(frozen=True)
class SweepSettings:
    """Runtime settings for a parallel parameter study."""

    max_parallel_jobs: int = 4
    timeout_s: int = 900
    force_rerun: bool = False
    generate_only: bool = False

    def __post_init__(self) -> None:
        if self.max_parallel_jobs < 1:
            raise ValueError("max_parallel_jobs must be >= 1")
        if self.timeout_s < 1:
            raise ValueError("timeout_s must be >= 1")


def _normalised_parameter_dict(params: SimulationParameters) -> dict:
    """Return a stable JSON-compatible parameter representation."""
    return asdict(params)


def _case_hash(params: SimulationParameters) -> str:
    payload = json.dumps(
        _normalised_parameter_dict(params),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:10]


def _safe_name(value: str) -> str:
    """Create a readable filesystem-safe label without external packages."""
    cleaned = []
    for char in value.strip():
        if char.isalnum() or char in {"-", "_", "."}:
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("_")
        else:
            cleaned.append("-")
    result = "".join(cleaned).strip("-_.")
    return result or "case"


def case_id(case: SweepCase) -> str:
    return f"{_safe_name(case.name)}_{_case_hash(case.parameters)}"


def _read_case_status(run_directory: Path) -> tuple[str, str | None]:
    """Read status/error from prototype.py's case.json when available."""
    metadata_file = run_directory / "case.json"
    if not metadata_file.is_file():
        return "unknown", None

    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "result_parse_failed", f"Could not read case.json: {exc}"

    status = str(metadata.get("febio_status", "unknown"))
    error = metadata.get("error")
    return status, str(error) if error is not None else None


def _cached_result(case: SweepCase, run_directory: Path) -> SweepResult | None:
    """Return an existing successful/partial result if the case is reusable."""
    parameter_file = run_directory / "parameters.json"
    if not parameter_file.is_file():
        return None

    try:
        existing = json.loads(parameter_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if existing != _normalised_parameter_dict(case.parameters):
        return None

    status, error = _read_case_status(run_directory)
    if status not in {"success", "partial"}:
        return None

    return SweepResult(
        name=case.name,
        case_id=case_id(case),
        run_directory=run_directory,
        return_code=0,
        status=status,
        elapsed_s=0.0,
        error=error,
        cached=True,
    )


def run_single_case(
    case: SweepCase,
    *,
    febio_path: str | Path,
    output_root: str | Path,
    timeout_s: int = 900,
    force_rerun: bool = False,
    generate_only: bool = False,
    project_root: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> SweepResult:
    """Run exactly one independent case through ``solver.prototype``.

    This function is safe to call concurrently as long as each case resolves
    to a unique parameter hash.  Every case owns its complete run directory.
    """
    if timeout_s < 1:
        raise ValueError("timeout_s must be >= 1")

    output_root = Path(output_root).resolve()
    run_directory = output_root / case_id(case)
    run_directory.mkdir(parents=True, exist_ok=True)

    if not force_rerun:
        cached = _cached_result(case, run_directory)
        if cached is not None:
            return cached

    parameter_file = run_directory / "parameters.json"
    parameter_file.write_text(
        json.dumps(
            _normalised_parameter_dict(case.parameters),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    stdout_file = run_directory / "launcher_stdout.txt"
    stderr_file = run_directory / "launcher_stderr.txt"

    python_executable = Path(python_executable or sys.executable).resolve()
    febio_path = Path(febio_path).resolve()

    if project_root is None:
        # solver/parallel_sweep.py -> project root
        project_root = Path(__file__).resolve().parents[1]
    else:
        project_root = Path(project_root).resolve()

    command = [
        str(python_executable),
        "-m",
        "solver.prototype",
        "--parameters",
        str(parameter_file),
        "--output",
        str(run_directory),
        "--timeout",
        str(timeout_s),
    ]

    if generate_only:
        command.append("--generate-only")
    else:
        if not febio_path.is_file():
            return SweepResult(
                name=case.name,
                case_id=case_id(case),
                run_directory=run_directory,
                return_code=None,
                status="launcher_failed",
                elapsed_s=0.0,
                error=f"FEBio executable not found: {febio_path}",
            )
        command.extend(["--febio", str(febio_path)])

    started = time.monotonic()

    try:
        with stdout_file.open("w", encoding="utf-8") as stdout, stderr_file.open(
            "w", encoding="utf-8"
        ) as stderr:
            completed = subprocess.run(
                command,
                cwd=project_root,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout_s + 30,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired as exc:
        return SweepResult(
            name=case.name,
            case_id=case_id(case),
            run_directory=run_directory,
            return_code=None,
            status="timeout",
            elapsed_s=time.monotonic() - started,
            error=str(exc),
        )
    except OSError as exc:
        return SweepResult(
            name=case.name,
            case_id=case_id(case),
            run_directory=run_directory,
            return_code=None,
            status="launcher_failed",
            elapsed_s=time.monotonic() - started,
            error=str(exc),
        )

    status, metadata_error = _read_case_status(run_directory)

    if completed.returncode != 0 and status in {"unknown", "success", "partial"}:
        status = "solver_failed"

    error = metadata_error
    if completed.returncode != 0 and not error:
        try:
            tail = stderr_file.read_text(encoding="utf-8", errors="replace").strip()
            if not tail:
                tail = stdout_file.read_text(encoding="utf-8", errors="replace").strip()
            if tail:
                error = tail[-4000:]
        except OSError:
            pass

    if generate_only and completed.returncode == 0 and status == "unknown":
        status = "generated"

    return SweepResult(
        name=case.name,
        case_id=case_id(case),
        run_directory=run_directory,
        return_code=completed.returncode,
        status=status,
        elapsed_s=time.monotonic() - started,
        error=error,
    )


def run_parallel_sweep(
    cases: Sequence[SweepCase] | Iterable[SweepCase],
    *,
    febio_path: str | Path,
    output_root: str | Path,
    max_parallel_jobs: int = 4,
    timeout_s: int = 900,
    force_rerun: bool = False,
    generate_only: bool = False,
    project_root: str | Path | None = None,
    python_executable: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[SweepResult]:
    """Run independent FEBio cases concurrently.

    Parameters
    ----------
    cases:
        Parameter-study cases.  Each case is isolated in its own directory.
    max_parallel_jobs:
        Maximum number of simultaneously running ``solver.prototype``/FEBio
        processes.  Start with 2 or 4; more is not automatically faster.
    progress_callback:
        Optional ``callback(result, completed_count, total_count)``.  It is
        called from this coordinator function's thread after each case ends.
        If this function itself runs inside a Qt worker thread, emit a Qt signal
        from the callback rather than touching widgets directly.
    cancel_event:
        Optional ``threading.Event``.  Once set, no new queued jobs are useful;
        futures that have not started are cancelled.  Already running FEBio
        processes are allowed to finish because forcibly killing nested FEBio
        restart jobs risks leaving incomplete case directories.
    """
    settings = SweepSettings(
        max_parallel_jobs=max_parallel_jobs,
        timeout_s=timeout_s,
        force_rerun=force_rerun,
        generate_only=generate_only,
    )

    case_list = list(cases)
    total = len(case_list)
    if total == 0:
        return []

    ids = [case_id(case) for case in case_list]
    if len(ids) != len(set(ids)):
        raise ValueError(
            "The sweep contains duplicate cases with identical name and parameters."
        )

    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[SweepResult] = []
    future_to_case: dict[Future[SweepResult], SweepCase] = {}

    worker_count = min(settings.max_parallel_jobs, total)

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="febio-case",
    ) as executor:
        for case in case_list:
            if cancel_event is not None and cancel_event.is_set():
                break

            future = executor.submit(
                run_single_case,
                case,
                febio_path=febio_path,
                output_root=output_root,
                timeout_s=settings.timeout_s,
                force_rerun=settings.force_rerun,
                generate_only=settings.generate_only,
                project_root=project_root,
                python_executable=python_executable,
            )
            future_to_case[future] = case

        completed_count = 0

        for future in as_completed(future_to_case):
            if cancel_event is not None and cancel_event.is_set():
                for pending in future_to_case:
                    if not pending.done():
                        pending.cancel()

            case = future_to_case[future]

            if future.cancelled():
                result = SweepResult(
                    name=case.name,
                    case_id=case_id(case),
                    run_directory=output_root / case_id(case),
                    return_code=None,
                    status="cancelled",
                    elapsed_s=0.0,
                    error=None,
                )
            else:
                try:
                    result = future.result()
                except Exception as exc:  # keep one bad case from killing the sweep
                    result = SweepResult(
                        name=case.name,
                        case_id=case_id(case),
                        run_directory=output_root / case_id(case),
                        return_code=None,
                        status="launcher_failed",
                        elapsed_s=0.0,
                        error=f"{type(exc).__name__}: {exc}",
                    )

            results.append(result)
            completed_count += 1

            if progress_callback is not None:
                progress_callback(result, completed_count, total)

    # Stable ordering makes downstream tables/heatmaps deterministic although
    # completion order is naturally non-deterministic.
    by_id = {result.case_id: result for result in results}
    ordered: list[SweepResult] = []
    for case in case_list:
        cid = case_id(case)
        if cid in by_id:
            ordered.append(by_id[cid])
        else:
            ordered.append(
                SweepResult(
                    name=case.name,
                    case_id=cid,
                    run_directory=output_root / cid,
                    return_code=None,
                    status="cancelled",
                    elapsed_s=0.0,
                )
            )
    return ordered


def recommended_parallel_jobs(*, reserve_cpus: int = 1, cap: int = 4) -> int:
    """Conservative default for this FEBio sweep workflow.

    FEBio itself can use CPU resources, so starting one job per logical CPU is
    usually counterproductive.  This helper intentionally caps the default at
    four concurrent simulations.  Benchmark 2 versus 4 on the target machine.
    """
    cpus = os.cpu_count() or 2
    usable = max(1, cpus - max(0, reserve_cpus))
    return max(1, min(cap, usable // 2 or 1))
