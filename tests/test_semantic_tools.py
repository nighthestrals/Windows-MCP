"""Semantic (accessibility-pattern) actions must never move the cursor."""

from types import SimpleNamespace

import pytest

from windows_mcp.tools import semantic


class FakePattern:
    def __init__(self, calls, label):
        self.calls = calls
        self.label = label

    def __getattr__(self, method):
        def recorded(*args):
            self.calls.append((f"{self.label}.{method}", args))
            return True

        return recorded


class FakeElement:
    def __init__(self, name="OK", patterns=None, enabled=True, parent=None):
        self.Name = name
        self.ControlTypeName = "Button"
        self.AutomationId = "ok"
        self.IsEnabled = enabled
        self._patterns = dict(patterns or {})
        self._parent = parent
        self.pattern_calls = []

    def GetPattern(self, pattern_id):
        label = self._patterns.get(pattern_id)
        if label is None:
            return None
        return FakePattern(self.pattern_calls, label)

    def GetParentControl(self):
        return self._parent


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, description=None, annotations=None):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


def _tools(desktop):
    mcp = FakeMCP()
    semantic.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


def _desktop(name="OK"):
    node = SimpleNamespace(name=name, center=SimpleNamespace(x=10, y=20), window_name="Test")
    return SimpleNamespace(get_node_from_label=lambda label: node)


def test_invoke_prefers_the_invoke_pattern(monkeypatch):
    element = FakeElement(patterns={semantic.uia.PatternId.InvokePattern: "invoke"})
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    result = _tools(_desktop())["InvokeElement"](label=0)
    assert result == {
        "ok": True,
        "method": "InvokePattern.Invoke",
        "name": "OK",
        "window": "Test",
        "cursor_moved": False,
    }
    assert element.pattern_calls == [("invoke.Invoke", ())]


def test_invoke_falls_back_to_selection_item(monkeypatch):
    element = FakeElement(patterns={semantic.uia.PatternId.SelectionItemPattern: "sel"})
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    assert _tools(_desktop())["InvokeElement"](label=1)["method"] == "SelectionItemPattern.Select"
    assert element.pattern_calls == [("sel.Select", ())]


def test_set_value_uses_the_value_pattern(monkeypatch):
    element = FakeElement(patterns={semantic.uia.PatternId.ValuePattern: "value"})
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    result = _tools(_desktop())["SetElementValue"](label=2, text="hello")
    assert result["method"] == "ValuePattern.SetValue"
    assert element.pattern_calls == [("value.SetValue", ("hello",))]


def test_set_value_reports_read_only_elements(monkeypatch):
    element = FakeElement(patterns={})
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    with pytest.raises(ValueError, match="可写"):
        _tools(_desktop())["SetElementValue"](label=3, text="x")


def test_select_uses_selection_item(monkeypatch):
    element = FakeElement(patterns={semantic.uia.PatternId.SelectionItemPattern: "sel"})
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    assert _tools(_desktop())["SelectElement"](label=4)["method"] == "SelectionItemPattern.Select"


def test_semantic_info_is_read_only(monkeypatch):
    element = FakeElement(
        name="OK", patterns={semantic.uia.PatternId.InvokePattern: "invoke"}
    )
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    info = _tools(_desktop())["SemanticInfo"](label=7)
    assert info["name"] == "OK"
    assert info["patterns"] == ["Invoke"]
    assert info["cursor_moved"] is False
    assert element.pattern_calls == []


def test_mismatched_element_is_refused(monkeypatch):
    element = FakeElement(name="Cancel")
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    with pytest.raises(ValueError, match="不匹配"):
        _tools(_desktop(name="OK"))["InvokeElement"](label=5)


def test_element_match_walks_up_to_the_parent(monkeypatch):
    parent = FakeElement(name="OK", patterns={semantic.uia.PatternId.InvokePattern: "invoke"})
    child = FakeElement(name="TextLabel", parent=parent)
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: child)
    result = _tools(_desktop(name="OK"))["InvokeElement"](label=6)
    assert result["method"] == "InvokePattern.Invoke"
    assert parent.pattern_calls == [("invoke.Invoke", ())]


def test_disabled_element_is_refused(monkeypatch):
    element = FakeElement(patterns={semantic.uia.PatternId.InvokePattern: "invoke"}, enabled=False)
    monkeypatch.setattr(semantic.uia, "ControlFromPoint", lambda x, y: element)
    with pytest.raises(ValueError, match="disabled"):
        _tools(_desktop())["InvokeElement"](label=8)
