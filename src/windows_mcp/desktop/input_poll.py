"""Cursor polling for machines where low-level input hooks never fire.

Only the cursor position is observable on such machines, which still provides
three usable signals:

* movement      - refresh the yellow warning and declare the user active
* shake gesture - toggle pause/resume without needing any mouse button
* injection gap - ignore movement the agent itself just injected
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
import time
from typing import Callable

from windows_mcp import input_activity

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.016
MOVE_EPSILON_PIXELS = 2
SHAKE_WINDOW_SECONDS = 1.2
# Deliberately strict: a stray back-and-forth while clicking a button must not
# toggle the pause, so the gesture needs both many reversals and real travel.
SHAKE_MIN_REVERSALS = 4
SHAKE_MIN_TRAVEL_PIXELS = 240
SHAKE_COOLDOWN_SECONDS = 1.0

_user32 = ctypes.windll.user32
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL


class ShakeDetector:
    """Counts horizontal direction reversals inside a short window.

    Pure logic, so the gesture can be regression-tested without a real mouse.
    """

    def __init__(
        self,
        window: float = SHAKE_WINDOW_SECONDS,
        min_reversals: int = SHAKE_MIN_REVERSALS,
        min_travel: float = SHAKE_MIN_TRAVEL_PIXELS,
        cooldown: float = SHAKE_COOLDOWN_SECONDS,
    ) -> None:
        self.window = window
        self.min_reversals = min_reversals
        self.min_travel = min_travel
        self.cooldown = cooldown
        self._reversals: list[float] = []
        self._travel: list[tuple[float, float]] = []
        self._last_sign = 0
        # Negative infinity so the first gesture is never suppressed by the
        # cooldown, whatever clock the caller uses.
        self._last_trigger = float("-inf")

    def reset(self) -> None:
        self._reversals.clear()
        self._travel.clear()
        self._last_sign = 0

    def feed(self, dx: float, dy: float, now: float) -> bool:
        """Record one movement sample; True when the shake gesture completes."""
        sign = 1 if dx > MOVE_EPSILON_PIXELS else -1 if dx < -MOVE_EPSILON_PIXELS else 0
        if not sign:
            return False
        if self._last_sign and sign != self._last_sign:
            self._reversals.append(now)
        self._last_sign = sign
        self._travel.append((now, abs(dx)))
        cutoff = now - self.window
        self._reversals = [stamp for stamp in self._reversals if stamp >= cutoff]
        self._travel = [entry for entry in self._travel if entry[0] >= cutoff]
        travel = sum(distance for _stamp, distance in self._travel)
        if (
            len(self._reversals) >= self.min_reversals
            and travel >= self.min_travel
            and now - self._last_trigger >= self.cooldown
        ):
            self._last_trigger = now
            self._reversals.clear()
            self._travel.clear()
            return True
        return False


class CursorPoller:
    """Turns cursor movement into warning flashes and the shake gesture."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_move: Callable[[], None] | None = None
        self._on_shake: Callable[[], None] | None = None
        self._enabled = False
        self._samples = 0
        self._moves = 0
        self._shakes = 0

    def configure(self, *, on_move: Callable[[], None], on_shake: Callable[[], None]) -> None:
        self._on_move = on_move
        self._on_shake = on_shake

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="windows-mcp-cursor-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread:
            thread.join(timeout=2.0)

    def set_enabled(self, enabled: bool) -> None:
        """Poll only while the AI owns the desktop or is paused."""
        self._enabled = enabled

    def status(self) -> dict:
        return {
            "enabled": self._enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "samples": self._samples,
            "moves": self._moves,
            "shakes": self._shakes,
        }

    def _run(self) -> None:
        last: tuple[int, int] | None = None
        detector = ShakeDetector()
        point = wintypes.POINT()
        while not self._stop.is_set():
            if not self._enabled:
                last = None
                detector.reset()
                self._stop.wait(0.05)
                continue
            if not _user32.GetCursorPos(ctypes.byref(point)):
                self._stop.wait(POLL_SECONDS)
                continue
            self._samples += 1
            position = (point.x, point.y)
            if input_activity.injecting():
                # The agent moved the cursor itself; never treat that as user input.
                last = position
                self._stop.wait(POLL_SECONDS)
                continue
            if last is not None:
                dx, dy = position[0] - last[0], position[1] - last[1]
                if abs(dx) >= MOVE_EPSILON_PIXELS or abs(dy) >= MOVE_EPSILON_PIXELS:
                    now = time.monotonic()
                    self._moves += 1
                    if self._on_move is not None:
                        try:
                            self._on_move()
                        except Exception:
                            logger.exception("Cursor move handler failed")
                    if detector.feed(dx, dy, now) and self._on_shake is not None:
                        self._shakes += 1
                        try:
                            self._on_shake()
                        except Exception:
                            logger.exception("Shake handler failed")
            last = position
            self._stop.wait(POLL_SECONDS)


_poller = CursorPoller()


def get_poller() -> CursorPoller:
    return _poller
