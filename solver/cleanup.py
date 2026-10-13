"""Remove large FEBio working artefacts after a successful result export."""

import json
import shutil
from pathlib import Path

from model.study_storage import atomic_text


def cleanup_successful_run(directory: Path) -> list[str]:
    """Keep only inputs, compact results and required visualization CSVs.

    This is intentionally called only after all exports and metadata have
    succeeded. Failed runs retain their logs and restart data for diagnosis.
    Cleanup is best-effort: a locked file must not invalidate exported results.
    """
    # solver.log is intentionally retained: the study post-processing and
    # optional preload validation still reference the run-local log after the
    # solver worker returns.  The large binary/branch artefacts are removed.
    directory = directory.resolve()
    warnings = []
    for name in ("model.xplt", "state.dmp", "stdout.txt", "stderr.txt", "trials", "checkpoints"):
        path = directory / name
        try:
            # Never follow a link/junction out of the explicitly scoped run.
            if path.resolve() != path:
                raise OSError("Aufräumziel ist eine Verknüpfung; es wird nicht gelöscht")
            if name in ("trials", "checkpoints"):
                if path.is_dir():
                    shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
        except OSError as error:
            warnings.append(f"Temporäre Dateien nicht vollständig gelöscht ({name}): {error}")
    return warnings


def finalize_successful_run(directory: Path, metadata: dict) -> list[str]:
    """Persist valid success/partial results before optional cleanup begins."""
    if metadata.get('febio_status') not in ('success', 'partial'):
        raise ValueError('Nur erfolgreich exportierte Ergebnisse dürfen aufgeräumt werden.')
    path = directory / 'case.json'
    metadata.update(temporary_files_cleaned=False, cleanup_warnings=[])
    # This first save is mandatory, just like the result export. If it fails,
    # retain all diagnostics and report the real persistence failure.
    atomic_text(path, json.dumps(metadata, indent=2, allow_nan=False))
    try:
        warnings = cleanup_successful_run(directory)
    except OSError as error:
        warnings = [f'Temporäre Dateien konnten nicht aufgeräumt werden: {error}']
    metadata.update(temporary_files_cleaned=not warnings, cleanup_warnings=warnings)
    try:
        atomic_text(path, json.dumps(metadata, indent=2, allow_nan=False))
    except OSError as error:
        # The valid status was saved above. Failure to update only the cleanup
        # bookkeeping is nonfatal too; the initial flag stays conservatively false.
        warnings.append(f'Aufräumstatus konnte nicht gespeichert werden: {error}')
        metadata['temporary_files_cleaned'] = False
    for warning in warnings:
        print(f'STATUS: Warnung: {warning} – exportierte Ergebnisse bleiben verfügbar.', flush=True)
    return warnings
