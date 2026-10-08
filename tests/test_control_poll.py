"""Cursor-poll fallback: shake gesture and injection guard."""

from windows_mcp import input_activity
from windows_mcp.desktop import input_poll


def test_shake_detector_needs_four_reversals_and_real_travel():
    detector = input_poll.ShakeDetector()
    samples = ((100, 0.0), (-100, 0.1), (100, 0.2), (-100, 0.3), (100, 0.4))
    results = [detector.feed(dx, 0, now) for dx, now in samples]
    assert results == [False, False, False, False, True]
    # The cooldown suppresses an immediate repeat.
    assert detector.feed(-100, 0, 0.5) is False
    assert detector.feed(100, 0, 0.6) is False


def test_shake_detector_ignores_short_reversals_without_travel():
    detector = input_poll.ShakeDetector()
    for index in range(10):
        assert detector.feed(10 if index % 2 else -10, 0, index * 0.05) is False


def test_shake_detector_forgets_reversals_outside_the_window():
    detector = input_poll.ShakeDetector()
    detector.feed(100, 0, 0.0)
    detector.feed(-100, 0, 0.1)
    assert detector.feed(100, 0, 5.0) is False


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
