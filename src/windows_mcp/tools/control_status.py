"""Read-only MCP control ownership status tool."""

from mcp.types import ToolAnnotations

from windows_mcp.desktop.control import get_controller


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="ControlStatus",
        description=(
            "Get the desktop control owner, the number of active AI calls, the "
            "remaining idle lease, and whether a resume still requires a fresh "
            "Snapshot observation before other tools are allowed."
        ),
        annotations=ToolAnnotations(
            title="ControlStatus",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def control_status_tool() -> dict:
        status = get_controller().status()
        try:
            from windows_mcp.desktop.hotkeys import get_hotkeys

            status["hotkeys"] = get_hotkeys().status()
        except Exception:
            pass
        try:
            from windows_mcp.desktop.input_poll import get_poller

            status["cursor_poll"] = get_poller().status()
        except Exception:
            pass
        return status
