"""Pure, displacement-controlled force targeting; no solver or UI dependencies."""

from dataclasses import dataclass
from math import isfinite

from model.parameters import SimulationParameters


@dataclass(frozen=True)
class AcceptedState:
    checkpoint_id: str
    drive_mm: float
    force_n: float
    solver_time: float


@dataclass(frozen=True)
class TrialRequest:
    source_checkpoint_id: str
    target_drive_mm: float


class ForceController:
    def __init__(self, params: SimulationParameters, initial: AcceptedState):
        if errors := params.validate():
            raise ValueError(" ".join(errors))
        self.params = params
        self.accepted = [initial]
        self.trials = 0
        self.upper_drive = None
        self.reason = None
        self.last_request = None

    @property
    def current(self):
        return self.accepted[-1]

    @property
    def contact_threshold(self):
        return max(1e-8, self.params.target_force_n * .001)

    def propose_trial(self) -> TrialRequest | None:
        p, state = self.params, self.current
        if self.reason:
            return None
        if self.trials >= p.max_control_trials:
            self.reason = "trial_limit"
            return None
        remaining = p.max_displacement_mm - state.drive_mm
        if remaining <= 1e-10:
            self.reason = "displacement_limit" if state.force_n > 1e-8 else "no_contact"
            return None
        step = p.approach_step_mm
        if len(self.accepted) >= 2 and state.force_n > self.contact_threshold:
            previous = self.accepted[-2]
            slope = (state.force_n - previous.force_n) / (state.drive_mm - previous.drive_mm)
            if slope > 0:
                step = .65 * (p.target_force_n - state.force_n) / slope
        step = min(max(step, p.min_drive_step_mm), p.max_drive_step_mm, remaining)
        if self.upper_drive is not None:
            step = min(step, .5 * (self.upper_drive - state.drive_mm))
        if step < p.min_drive_step_mm * (1 - 1e-8):
            self.reason = "minimum_step_reached"
            return None
        self.last_request = TrialRequest(state.checkpoint_id, state.drive_mm + step)
        return self.last_request

    def observe_converged(self, state: AcceptedState) -> tuple[bool, str]:
        self._check_pending()
        self.trials += 1
        request, self.last_request = self.last_request, None
        if not all(isfinite(v) for v in (state.drive_mm, state.force_n, state.solver_time)):
            raise ValueError("Non-finite trial result")
        if abs(state.drive_mm - request.target_drive_mm) > 1e-7 or state.solver_time <= self.current.solver_time:
            raise ValueError("Trial endpoint does not match the requested advance")
        p = self.params
        if state.force_n < self.current.force_n - max(p.force_tolerance_n, self.contact_threshold):
            self.reason = "nonmonotonic_response"
            return False, self.reason
        if state.force_n > p.target_force_n + p.force_tolerance_n:
            self.upper_drive = state.drive_mm
            return False, "overshoot"
        self.accepted.append(state)
        # A broad tolerance may include zero for small targets. Never report
        # free approach (no measurable contact) as a reached pressing force.
        if state.force_n > self.contact_threshold and abs(state.force_n - p.target_force_n) <= p.force_tolerance_n:
            self.reason = "target_reached"
        return True, self.reason or "accepted"

    def observe_failed(self) -> str:
        self._check_pending()
        self.trials += 1
        self.upper_drive = self.last_request.target_drive_mm
        self.last_request = None
        return "solver_failed_retry"

    def _check_pending(self):
        if self.last_request is None:
            raise ValueError("No pending trial")
