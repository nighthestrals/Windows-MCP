"""Accessibility-pattern actions that never move the shared cursor.

Coordinate clicks and injected keys fight with a human using the same pointer.
These tools drive the element's own UI Automation pattern instead (Invoke,
Value, SelectionItem, Toggle, ExpandCollapse), so the agent's action and the
user's mouse stay independent. Nothing here calls SetCursorPos or SendInput.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import ToolAnnotations

from windows_mcp import uia

logger = logging.getLogger(__name__)

_PATTERN_LABELS = {
    uia.PatternId.InvokePattern: "Invoke",
    uia.PatternId.ValuePattern: "Value",
    uia.PatternId.SelectionItemPattern: "SelectionItem",
    uia.PatternId.TogglePattern: "Toggle",
    uia.PatternId.ExpandCollapsePattern: "ExpandCollapse",
    uia.PatternId.RangeValuePattern: "RangeValue",
    uia.PatternId.LegacyIAccessiblePattern: "LegacyIAccessible",
    uia.PatternId.ScrollItemPattern: "ScrollItem",
}


def _element_for_label(desktop: Any, label: int) -> tuple[Any, Any]:
    """Resolve a Snapshot label to a live element, refusing a mismatched hit."""
    node = desktop.get_node_from_label(label)
    center = node.center
    element = uia.ControlFromPoint(center.x, center.y)
    if element is None:
        raise ValueError(
            f"label {label} 的位置没有无障碍元素；界面可能已变化，请重新 Snapshot"
        )
    wanted = (node.name or "").strip()
    candidate = element
    for _ in range(4):
        if candidate is None:
            break
        name = (getattr(candidate, "Name", "") or "").strip()
        if not wanted or name == wanted or wanted in name or name in wanted:
            if not getattr(candidate, "IsEnabled", True):
                raise ValueError(f"{name!r} 当前不可用（disabled），请换一个元素或稍后重试")
            return node, candidate
        candidate = candidate.GetParentControl()
    raise ValueError(
        f"label {label} 指向 {wanted!r}，但该位置的无障碍元素是 {element.Name!r}（不匹配）："
        "窗口可能被遮挡或界面已变化，请重新 Snapshot 后再试"
    )


def _available(element: Any) -> list[str]:
    found = []
    for pattern_id, label in _PATTERN_LABELS.items():
        try:
            if element.GetPattern(pattern_id) is not None:
                found.append(label)
        except Exception:
            continue
    return found


def _try(element: Any, pattern_id: int, method: str, *args: Any) -> bool:
    pattern = element.GetPattern(pattern_id)
    if pattern is None:
        return False
    action = getattr(pattern, method, None)
    if action is None:
        return False
    action(*args)
    return True


def register(mcp, *, get_desktop, get_analytics):
    annotations = ToolAnnotations

    @mcp.tool(
        name="SemanticInfo",
        description=(
            "Inspect one UI element by the label a Snapshot gave it: name, control "
            "type, automation id, enabled state and which accessibility patterns it "
            "supports. Read-only and never moves the cursor; call it before choosing "
            "between InvokeElement, SetElementValue, SelectElement and Click."
        ),
        annotations=annotations(
            title="SemanticInfo",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def semantic_info_tool(label: int) -> dict:
        node, element = _element_for_label(get_desktop(), label)
        return {
            "label": label,
            "name": element.Name,
            "control_type": element.ControlTypeName,
            "automation_id": element.AutomationId,
            "enabled": bool(element.IsEnabled),
            "window": node.window_name,
            "patterns": _available(element),
            "cursor_moved": False,
        }

    @mcp.tool(
        name="InvokeElement",
        description=(
            "Activate a UI element by its Snapshot label through its accessibility "
            "pattern (Invoke/SelectionItem/Toggle/Expand) instead of clicking "
            "coordinates. Prefer this over Click whenever the element supports a "
            "pattern: it never moves the shared mouse cursor, so the user's own mouse "
            "work cannot disturb the action. Raises an error when the element offers "
            "no pattern, in which case Click is the fallback."
        ),
        annotations=annotations(
            title="InvokeElement",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def invoke_element_tool(label: int) -> dict:
        node, element = _element_for_label(get_desktop(), label)
        chain = (
            ("InvokePattern.Invoke", uia.PatternId.InvokePattern, "Invoke"),
            ("SelectionItemPattern.Select", uia.PatternId.SelectionItemPattern, "Select"),
            ("TogglePattern.Toggle", uia.PatternId.TogglePattern, "Toggle"),
            ("ExpandCollapsePattern.Expand", uia.PatternId.ExpandCollapsePattern, "Expand"),
            (
                "LegacyIAccessiblePattern.DoDefaultAction",
                uia.PatternId.LegacyIAccessiblePattern,
                "DoDefaultAction",
            ),
        )
        for method_label, pattern_id, method in chain:
            try:
                if _try(element, pattern_id, method):
                    return {
                        "ok": True,
                        "method": method_label,
                        "name": element.Name,
                        "window": node.window_name,
                        "cursor_moved": False,
                    }
            except Exception as exc:
                logger.debug("pattern %s failed: %s", method_label, exc)
        raise ValueError(
            f"{element.Name!r} 不提供任何可用的无障碍动作（"
            + ", ".join(_available(element) or ["none"])
            + "）；请改用 Click"
        )

    @mcp.tool(
        name="SetElementValue",
        description=(
            "Set a text or value element's content by its Snapshot label through the "
            "Value/RangeValue/LegacyIAccessible pattern, instead of focusing it and "
            "typing. No cursor movement and no keyboard injection, so the user's mouse "
            "and keyboard stay untouched. Raises an error when the element is read-only."
        ),
        annotations=annotations(
            title="SetElementValue",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def set_element_value_tool(label: int, text: str) -> dict:
        node, element = _element_for_label(get_desktop(), label)
        if _try(element, uia.PatternId.ValuePattern, "SetValue", str(text)):
            return {
                "ok": True,
                "method": "ValuePattern.SetValue",
                "name": element.Name,
                "window": node.window_name,
                "cursor_moved": False,
            }
        if _try(element, uia.PatternId.LegacyIAccessiblePattern, "SetValue", str(text)):
            return {
                "ok": True,
                "method": "LegacyIAccessiblePattern.SetValue",
                "name": element.Name,
                "window": node.window_name,
                "cursor_moved": False,
            }
        try:
            numeric = float(text)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and _try(
            element, uia.PatternId.RangeValuePattern, "SetValue", numeric
        ):
            return {
                "ok": True,
                "method": "RangeValuePattern.SetValue",
                "name": element.Name,
                "window": node.window_name,
                "cursor_moved": False,
            }
        raise ValueError(
            f"{element.Name!r} 不是可写元素（只读或不支持 SetValue）；"
            "请改用 Snapshot 定位后 Click 聚焦再 Type"
        )

    @mcp.tool(
        name="SelectElement",
        description=(
            "Select a list item, tab, radio button or combo entry by its Snapshot "
            "label through SelectionItem/ExpandCollapse/Invoke. Prefer it over clicking "
            "coordinates: it never moves the shared mouse cursor."
        ),
        annotations=annotations(
            title="SelectElement",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def select_element_tool(label: int) -> dict:
        node, element = _element_for_label(get_desktop(), label)
        chain = (
            ("SelectionItemPattern.Select", uia.PatternId.SelectionItemPattern, "Select"),
            ("ExpandCollapsePattern.Expand", uia.PatternId.ExpandCollapsePattern, "Expand"),
            ("InvokePattern.Invoke", uia.PatternId.InvokePattern, "Invoke"),
            (
                "LegacyIAccessiblePattern.Select",
                uia.PatternId.LegacyIAccessiblePattern,
                "Select",
            ),
        )
        for method_label, pattern_id, method in chain:
            try:
                if method == "Select" and pattern_id == uia.PatternId.LegacyIAccessiblePattern:
                    # LegacyIAccessiblePattern.Select takes a selection flag.
                    if _try(element, pattern_id, method, 1):
                        return {
                            "ok": True,
                            "method": method_label,
                            "name": element.Name,
                            "window": node.window_name,
                            "cursor_moved": False,
                        }
                    continue
                if _try(element, pattern_id, method):
                    return {
                        "ok": True,
                        "method": method_label,
                        "name": element.Name,
                        "window": node.window_name,
                        "cursor_moved": False,
                    }
            except Exception as exc:
                logger.debug("pattern %s failed: %s", method_label, exc)
        raise ValueError(f"{element.Name!r} 不支持选择动作；请改用 Click")
