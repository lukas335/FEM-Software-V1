from model.parameters import ShellParameters, SimulationParameters
from model.units import mmhg_to_mpa


def test_baseline_parameters_are_valid():
    params = SimulationParameters(ShellParameters(8, 1.25, "silicone_A30"), ShellParameters(22, 1, "silicone_A30"))
    assert params.validate() == []


def test_invalid_wall_is_rejected():
    assert ShellParameters(1, 1, "silicone_A30").validate()


def test_pressure_conversion():
    assert abs(mmhg_to_mpa(15) - 0.00199983) < 1e-8
