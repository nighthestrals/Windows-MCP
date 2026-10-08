"""Process-local marker for windows where the agent injects input.

The cursor poller must never mistake the agent's own injected movement for the
user's, so every low-level input primitive stamps this marker first. The module
has no project imports on purpose: the vendored UIA layer imports it.
"""

from __future__ import annotations

import time

# Long enough to cover a click's move-press-release burst, short enough that a
# real user movement right after an action is still noticed.
INJECTION_GRACE_SECONDS = 0.25

_injected_until = 0.0


def mark_injection(grace: float = INJECTION_GRACE_SECONDS) -> None:
    """Declare that input injected from now on is the agent's, not the user's."""
    global _injected_until
    _injected_until = time.monotonic() + grace


def injecting() -> bool:
    """True while recently injected input may still be settling."""
    return _injected_until > time.monotonic()


def reset() -> None:
    """Test helper: forget any pending injection window."""
    global _injected_until
    _injected_until = 0.0
