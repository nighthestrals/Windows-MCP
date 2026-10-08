"""Explicit resume after the user stopped desktop control."""

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from windows_mcp.desktop.control import get_controller


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="ControlResume",
        description=(
            "Re-enable Windows desktop control after the user stopped it with the "
            "exit hotkey (Ctrl+Alt+Shift+F12). Only call this when the user "
            "explicitly asks to resume. Requires confirm=true. The first tool call "
            "after resuming must be Snapshot."
        ),
        annotations=ToolAnnotations(
            title="ControlResume",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def control_resume_tool(confirm: bool = False) -> dict:
        if not confirm:
            raise ToolError(
                '{"code": "RESUME_CONFIRMATION_REQUIRED", "message": '
                '"Pass confirm=true only after the user asked to resume control."}'
            )
        controller = get_controller()
        resumed = controller.exit_disabled()
        return {"resumed": resumed, "status": controller.status()}
