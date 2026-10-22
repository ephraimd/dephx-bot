from typing import Any

from dephx_bot.agent.approvals import ApprovalRequest, Decision
from dephx_bot.settings.project import Project
from dephx_bot.tools import EDIT_PROJECT_SETTINGS, Tool, ToolContext, ToolError, ToolResult
from dephx_bot.tools.shell import hidden_characters


def make_project_settings_tool(project: Project) -> Tool:
    """Lets the agent help with the project's dephx-bot settings. Nothing changes until the user applies it."""

    async def edit_project_settings(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if project.pending_trust:
            raise ToolError(
                "The user has not yet decided whether to trust this project's settings. Ask them to do that first."
            )
        allow = _commands(args, "allow")
        remove = _commands(args, "remove")
        current = project.allowed_commands()
        updated = [c for c in current if c not in remove] + [c for c in dict.fromkeys(allow) if c not in current]
        if updated == current:
            raise ToolError("That leaves the settings as they are, so there is nothing to change.")

        added = [c for c in updated if c not in current]
        dropped = [c for c in current if c not in updated]
        change = "\n".join([*(f"+ {c}" for c in added), *(f"- {c}" for c in dropped)])
        answer = await ctx.request_approval(ApprovalRequest("settings", f"Commands that run without asking:\n{change}"))
        if answer.decision != Decision.APPROVE:
            return ToolResult("The user did not apply this change.", ok=False)

        project.set_allowed_commands(updated)
        listed = "\n".join(updated) or "(none)"
        return ToolResult(f"Applied. Commands that now run without asking:\n{listed}")

    return Tool(
        EDIT_PROJECT_SETTINGS,
        "Change which commands run in this project without asking the user. The user sees the change and "
        "must apply it. Use this when the user asks for help setting up dephx-bot for their project.",
        {
            "type": "object",
            "properties": {
                "allow": {"type": "array", "items": {"type": "string"}, "description": "Exact commands to allow."},
                "remove": {"type": "array", "items": {"type": "string"}, "description": "Exact commands to stop allowing."},
            },
        },
        edit_project_settings,
    )


def _commands(args: dict[str, Any], name: str) -> list[str]:
    value = args.get(name, [])
    if not isinstance(value, list) or not all(isinstance(c, str) and c.strip() for c in value):
        raise ToolError(f"Invalid argument '{name}': expected a list of commands.")
    commands = [c.strip() for c in value]
    for command in commands:
        hidden = hidden_characters(command)
        if hidden:
            raise ToolError(f"The command '{command}' contains hidden characters ({hidden}). Use plain text.")
    return commands
