"""Global hotkey ownership, exercised without touching the real desktop."""

import pytest

from windows_mcp.desktop import hotkeys


class FakeUser32:
    def __init__(self) -> None:
        self.registered: dict[int, tuple[int, int]] = {}
        self.fail_for: set[int] = set()
        self.unregistered: list[int] = []

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, virtual_key):
        if hotkey_id in self.fail_for:
            return 0
        self.registered[hotkey_id] = (modifiers, virtual_key)
        return 1

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregistered.append(hotkey_id)
        self.registered.pop(hotkey_id, None)
        return 1

    def PeekMessageW(self, *args, **kwargs):
        return 0

    def TranslateMessage(self, message):
        return 1

    def DispatchMessageW(self, message):
        return 1


@pytest.fixture
def manager(monkeypatch):
    fake = FakeUser32()
    monkeypatch.setattr(hotkeys, "_user32", fake)
    instance = hotkeys.HotkeyManager()
    instance.start()
    try:
        yield instance, fake
    finally:
        instance.stop()


def test_enable_registers_both_combos_and_disable_releases_them(manager):
    instance, fake = manager
    assert instance.enable() is True
    assert set(fake.registered) == {hotkeys.PAUSE_ID, hotkeys.EXIT_ID}
    assert instance.status()["registered"] is True
    assert instance.disable() is True
    assert fake.registered == {}
    assert instance.status()["conflicts"] == []


def test_conflicting_combo_is_reported_not_hidden(manager):
    instance, fake = manager
    fake.fail_for.add(hotkeys.PAUSE_ID)
    assert instance.enable() is True
    assert hotkeys.PAUSE_COMBO[0] in instance.status()["conflicts"]
    assert hotkeys.EXIT_ID in fake.registered


def test_hotkey_messages_route_to_their_callbacks(manager):
    instance, fake = manager
    seen: list[str] = []
    instance.configure(
        on_pause=lambda: seen.append("pause"),
        on_exit=lambda: seen.append("exit"),
    )
    instance._dispatch(hotkeys.PAUSE_ID)
    instance._dispatch(hotkeys.EXIT_ID)
    instance._dispatch(99)
    assert seen == ["pause", "exit"]


def test_stop_releases_registrations(monkeypatch):
    fake = FakeUser32()
    monkeypatch.setattr(hotkeys, "_user32", fake)
    instance = hotkeys.HotkeyManager()
    instance.start()
    instance.enable()
    instance.stop()
    assert fake.registered == {}
    assert instance.status()["running"] is False
