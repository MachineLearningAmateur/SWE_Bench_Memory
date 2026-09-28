"""The predeclared calibration rule applies to the full 12-task denominator."""

import pytest

from src.gpt_oss_calibration_report import gate_for_resolved


@pytest.mark.parametrize(
    ("resolved", "expected"),
    [
        (0, "floor_stop"), (2, "floor_stop"),
        (3, "pilot_eligible_if_graph_frozen"),
        (9, "pilot_eligible_if_graph_frozen"),
        (10, "ceiling_stop"), (12, "ceiling_stop"),
    ],
)
def test_frozen_gate(resolved, expected):
    assert gate_for_resolved(resolved) == expected


def test_partial_batch_has_no_gate_decision():
    with pytest.raises(ValueError):
        gate_for_resolved(2, total=11)
