"""Cursor-poll fallback: shake gesture and injection guard."""

from windows_mcp import input_activity
from windows_mcp.desktop import input_poll


def test_shake_detector_needs_three_reversals_inside_the_window():
    detector = input_poll.ShakeDetector()
    assert detector.feed(10, 0, 0.0) is False
    assert detector.feed(-10, 0, 0.1) is False  # first reversal
    assert detector.feed(10, 0, 0.2) is False  # second reversal
    assert detector.feed(-10, 0, 0.3) is True  # third reversal completes it
    # The cooldown suppresses an immediate repeat.
    assert detector.feed(10, 0, 0.4) is False
    assert detector.feed(-10, 0, 0.5) is False
    assert detector.feed(10, 0, 0.6) is False


def test_shake_detector_forgets_reversals_outside_the_window():
    detector = input_poll.ShakeDetector()
    detector.feed(10, 0, 0.0)
    detector.feed(-10, 0, 0.1)
    assert detector.feed(10, 0, 5.0) is False


def test_shake_detector_ignores_sub_epsilon_jitter():
    detector = input_poll.ShakeDetector()
    for index in range(10):
        assert detector.feed(1, 0, index * 0.01) is False


def test_injection_marker_round_trip():
    input_activity.reset()
    assert input_activity.injecting() is False
    input_activity.mark_injection(grace=5.0)
    assert input_activity.injecting() is True
    input_activity.reset()
    assert input_activity.injecting() is False
