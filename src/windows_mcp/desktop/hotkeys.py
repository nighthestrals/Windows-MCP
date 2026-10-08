"""Global hotkey ownership through RegisterHotKey.

Low-level input hooks are blocked by endpoint-management software on some
machines, but RegisterHotKey keeps working: the window manager matches the
combination itself and posts WM_HOTKEY to the registering thread. That makes it
the only reliable keyboard escape hatch in such environments.

Registration is exclusive - while a combination is held it no longer reaches the
foreground application - so the coordinator enables the manager only while the
AI holds control (or is paused) and releases it again on idle and shutdown.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
VK_BACK = 0x08

PAUSE_ID = 1
EXIT_ID = 2

# (label, modifiers, virtual key, hotkey id)
PAUSE_COMBO = ("Ctrl+Backspace", MOD_CONTROL, VK_BACK, PAUSE_ID)
EXIT_COMBO = ("Ctrl+Alt+Shift+Backspace", MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_BACK, EXIT_ID)
COMBOS = (PAUSE_COMBO, EXIT_COMBO)

_user32 = ctypes.windll.user32


class HotkeyManager:
    """Owns the global hotkey registrations on one message-pumping thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._cond = threading.Condition()
        self._commands: list[str] = []
        self._acks = 0
        self._registered = False
        self._conflicts: list[str] = []
        self._last_error = 0
        self._pause_cb: Callable[[], None] | None = None
        self._exit_cb: Callable[[], None] | None = None

    def configure(self, *, on_pause: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self._pause_cb = on_pause
        self._exit_cb = on_exit

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="windows-mcp-hotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(3.0):
            raise RuntimeError("Global hotkey thread did not start")

    def stop(self) -> None:
        self._submit("disable", 1.0)
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        thread, self._thread = self._thread, None
        if thread:
            thread.join(timeout=2.0)

    def enable(self, timeout: float = 2.0) -> bool:
        return self._submit("enable", timeout)

    def disable(self, timeout: float = 2.0) -> bool:
        return self._submit("disable", timeout)

    def status(self) -> dict:
        return {
            "registered": self._registered,
            "conflicts": list(self._conflicts),
            "last_error": self._last_error,
            "running": bool(self._thread and self._thread.is_alive()),
            "combos": [combo[0] for combo in COMBOS],
        }

    def _submit(self, command: str, timeout: float) -> bool:
        with self._cond:
            if not (self._thread and self._thread.is_alive()):
                return False
            target = self._acks + 1
            self._commands.append(command)
            self._cond.notify_all()
            deadline = time.monotonic() + timeout
            while self._acks < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def _run(self) -> None:
        self._ready.set()
        message = wintypes.MSG()
        while not self._stop.is_set():
            with self._cond:
                if not self._commands and not self._stop.is_set():
                    self._cond.wait(0.05)
                commands, self._commands = self._commands, []
            for command in commands:
                try:
                    if command == "enable":
                        self._do_enable()
                    else:
                        self._do_disable()
                except Exception:
                    self._last_error = ctypes.get_last_error()
                    logger.exception("Global hotkey command failed")
                with self._cond:
                    self._acks += 1
                    self._cond.notify_all()
            while _user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                if message.message == WM_HOTKEY:
                    self._dispatch(int(message.wParam))
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))

    def _dispatch(self, hotkey_id: int) -> None:
        if hotkey_id == PAUSE_ID:
            callback = self._pause_cb
        elif hotkey_id == EXIT_ID:
            callback = self._exit_cb
        else:
            callback = None
        if callback is None:
            return
        try:
            # The coordinator's request handlers only set flags and wake its
            # watchdog, so they are safe to call straight from this thread.
            callback()
        except Exception:
            logger.exception("Global hotkey callback failed")

    def _do_enable(self) -> None:
        conflicts: list[str] = []
        last_error = 0
        for label, modifiers, virtual_key, hotkey_id in COMBOS:
            _user32.UnregisterHotKey(None, hotkey_id)
            ctypes.set_last_error(0)
            if not _user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, virtual_key):
                last_error = ctypes.get_last_error()
                conflicts.append(label)
                logger.warning("Global hotkey %s unavailable (error %d)", label, last_error)
        self._conflicts = conflicts
        self._last_error = last_error
        self._registered = True

    def _do_disable(self) -> None:
        for _label, _modifiers, _virtual_key, hotkey_id in COMBOS:
            _user32.UnregisterHotKey(None, hotkey_id)
        self._registered = False
        self._conflicts = []


_hotkeys = HotkeyManager()


def get_hotkeys() -> HotkeyManager:
    return _hotkeys
