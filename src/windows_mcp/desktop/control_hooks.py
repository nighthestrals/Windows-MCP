"""Fast physical-input hook callbacks; never wait for UI or MCP work.

DSH fork behaviour:

* Mouse movement never transfers ownership. While the AI holds an active call
  the movement is still swallowed, but the only visible effect is the yellow
  warning flash on the indicator.
* A physical left double click toggles the explicit pause: it pauses while the
  AI holds control and resumes while paused. Both clicks are consumed so the
  gesture can never reach the application underneath.
* The Ctrl+Alt+Shift+Backspace chord toggles the same pause from the keyboard.
"""

import ctypes
import time
from typing import Any

from windows_mcp.desktop.control_win32 import (
    _CHORD,
    _KeyHookData,
    _MouseHookData,
    _key,
    _mouse_button,
    _read_raw_mouse,
    _user32,
)

# Physical clicks closer together than this count as the pause/resume gesture.
_GESTURE_MAX_MOVE_PIXELS = 6
# Swallow the tail of a recognized gesture burst so the application never sees
# a half-delivered double click.
_GESTURE_SWALLOW_SECONDS = 0.35
# The gesture is only meaningful while the AI holds control or is paused.
_GESTURE_STATES = ("ai", "paused")


def _mark_move(owner: Any) -> None:
    """Refresh the yellow warning without letting a hook fault escape."""
    marker = getattr(owner, "mark_user_move", None)
    if marker is not None:
        try:
            marker()
        except Exception:
            pass


def _request_gesture(owner: Any) -> None:
    request = getattr(owner, "request_gesture", None)
    if request is not None:
        request()


def _double_click_timeout(owner: Any) -> float:
    try:
        timeout = int(_user32.GetDoubleClickTime())
    except Exception:
        timeout = 0
    if timeout <= 0:
        timeout = 500
    return timeout / 1000.0


def _is_gesture_click(owner: Any, x: int, y: int, now: float) -> bool:
    """Track physical left-button downs and detect the double-click gesture."""
    if now - owner._last_click_at <= _double_click_timeout(owner):
        ox, oy = owner._last_click_pos
        if abs(x - ox) <= _GESTURE_MAX_MOVE_PIXELS and abs(y - oy) <= _GESTURE_MAX_MOVE_PIXELS:
            owner._last_click_at = 0.0
            return True
    owner._last_click_at = now
    owner._last_click_pos = (x, y)
    return False


def physical_mouse(owner: Any, code: int, wparam: int, lparam: int) -> int:
    """Handle a physical mouse hook callback without waiting on other threads."""
    if code < 0:
        return _user32.CallNextHookEx(owner._mouse_hook, code, wparam, lparam)
    valid = False
    button = None
    try:
        data = ctypes.cast(lparam, ctypes.POINTER(_MouseHookData)).contents
        if data.flags & 1:  # LL mouse injection flags: AI input is never a gesture.
            return _user32.CallNextHookEx(owner._mouse_hook, code, wparam, lparam)
        valid = True
        now = time.monotonic()
        owner._last_physical_event = now
        button = _mouse_button(wparam, data.mouseData)
        if button:
            if button[1]:
                owner._mouse_down.add(button[0])
            else:
                owner._mouse_down.discard(button[0])
        # Physical movement only refreshes the warning; it never changes owner.
        if wparam == 0x200 and owner._state == "ai":
            _mark_move(owner)
        if owner._gesture_swallow_until > now:
            return 1
        if (
            button
            and button[0] == 1
            and button[1]
            and owner._state in _GESTURE_STATES
            and _is_gesture_click(owner, data.pt.x, data.pt.y, now)
        ):
            owner._gesture_swallow_until = now + _GESTURE_SWALLOW_SECONDS
            _request_gesture(owner)
            return 1
        owner._queue(("point", data.pt.x, data.pt.y, int(wparam)))
        if owner._suppress and owner._active_calls > 0 and not owner._emergency:
            if now <= owner._deadline:
                return 1
            owner._fail_open()
    except Exception:
        owner._fail_open()
    if valid and button:
        if button[1]:
            owner._delivered_mouse.add(button[0])
        else:
            owner._delivered_mouse.discard(button[0])
    return _user32.CallNextHookEx(owner._mouse_hook, code, wparam, lparam)


def physical_key(owner: Any, code: int, wparam: int, lparam: int) -> int:
    """Track each physical key independently and honor the pause chord."""
    if code < 0:
        return _user32.CallNextHookEx(owner._key_hook, code, wparam, lparam)
    vk = None
    down = False
    try:
        data = ctypes.cast(lparam, ctypes.POINTER(_KeyHookData)).contents
        if data.flags & 0x10:  # LLKHF_INJECTED: AI SendInput stays usable.
            return _user32.CallNextHookEx(owner._key_hook, code, wparam, lparam)
        owner._last_physical_event = time.monotonic()
        vk = int(data.vkCode)
        down = wparam in (0x100, 0x104)
        was_down = vk in owner._pressed
        if down:
            owner._pressed.add(vk)
        else:
            owner._pressed.discard(vk)
        if vk in owner._quarantine:
            if not down:
                owner._quarantine.discard(vk)
                owner._queue(("key",))  # Idle starts after the last chord key is released.
            return 1
        if (
            down
            and vk == 0x08
            and not was_down
            and not owner._fast_takeover
            and _CHORD.issubset({_key(held) for held in owner._pressed})
        ):
            # The chord toggles the explicit pause in every ownership state, so
            # a user whose pointer input is swallowed always has an exit.
            owner._quarantine.update(held for held in owner._pressed if _key(held) in _CHORD)
            owner._fast_takeover = True
            owner._suppress = False  # Release first, then tell the coordinator.
            owner.input_ledger.block_new()
            owner._queue(("hotkey",))
            return 1
        owner._queue(("key",))
        if owner._suppress and owner._active_calls > 0 and not owner._emergency:
            if time.monotonic() <= owner._deadline:
                return 1
            owner._fail_open()
    except Exception:
        owner._fail_open()
    if vk is not None and down:
        owner._delivered_keys.add(vk)
    elif vk is not None:
        owner._delivered_keys.discard(vk)
    return _user32.CallNextHookEx(owner._key_hook, code, wparam, lparam)


def raw_input(owner: Any, lparam: int) -> None:
    """Queue relative mouse data from a device, independent of injected input."""
    raw = _read_raw_mouse(lparam)
    if raw is None:
        return  # Null devices can be touchpads; do not infer source.
    owner._last_physical_event = time.monotonic()
    if owner._state == "ai":
        _mark_move(owner)
    owner._queue(("raw", *raw))
