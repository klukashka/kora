from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal, cast

from kora.plugins.harness.claude.configuration import CLI_VERSION
from kora.protocols.harness import RunnerCause

ToolKind = Literal["file_edit", "shell", "subagent", "hosted_tool", "other_tool"]
REVIEWED_PLUGINS: Final[frozenset[str]] = frozenset(
    {"cc-plugin-agents-md", "cc-plugin-plugin-authoring"}
)
REVIEWED_TOOLS: Final[Mapping[str, ToolKind]] = MappingProxyType(
    {
        "Task": "subagent",
        "Bash": "shell",
        "CronCreate": "other_tool",
        "CronDelete": "other_tool",
        "CronList": "other_tool",
        "Edit": "file_edit",
        "EnterWorktree": "other_tool",
        "ExitWorktree": "other_tool",
        "ListAgents": "other_tool",
        "NotebookEdit": "file_edit",
        "Read": "other_tool",
        "ReportFindings": "other_tool",
        "ScheduleWakeup": "other_tool",
        "SendMessage": "other_tool",
        "Skill": "other_tool",
        "TaskCreate": "other_tool",
        "TaskGet": "other_tool",
        "TaskList": "other_tool",
        "TaskStop": "other_tool",
        "TaskUpdate": "other_tool",
        "WebFetch": "hosted_tool",
        "WebSearch": "hosted_tool",
        "Workflow": "other_tool",
        "Write": "file_edit",
    }
)


def named_entries(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for entry in cast("list[object]", value):
        if not isinstance(entry, dict):
            return None
        name = cast("dict[object, object]", entry).get("name")
        if not isinstance(name, str):
            return None
        names.append(name)
    return names


def surface_cause(data: object, *, output_format: bool) -> RunnerCause | None:
    if not isinstance(data, dict):
        return "plugin_mismatch"
    fields = cast("dict[object, object]", data)
    version = fields.get("claude_code_version")
    if not isinstance(version, str):
        return "plugin_mismatch"
    if version != CLI_VERSION:
        return "version_mismatch"
    plugins = named_entries(fields.get("plugins"))
    if (
        plugins is None
        or len(plugins) != len(set(plugins))
        or frozenset(plugins) != REVIEWED_PLUGINS
    ):
        return "plugin_mismatch"
    tools = fields.get("tools")
    if not isinstance(tools, list):
        return "plugin_mismatch"
    names: list[str] = []
    for tool in cast("list[object]", tools):
        if not isinstance(tool, str):
            return "plugin_mismatch"
        names.append(tool)
    if len(names) != len(set(names)):
        return "plugin_mismatch"
    builtins = {name for name in names if not name.startswith("mcp__")}
    if output_format:
        builtins.discard("StructuredOutput")
    if not builtins <= REVIEWED_TOOLS.keys():
        return "plugin_mismatch"
    servers = fields.get("mcp_servers")
    if not isinstance(servers, list):
        return "plugin_mismatch"
    sources: list[str] = []
    for server in cast("list[object]", servers):
        if not isinstance(server, dict):
            return "plugin_mismatch"
        source = cast("dict[object, object]", server).get("source")
        if source is not None:
            if not isinstance(source, str):
                return "plugin_mismatch"
            sources.append(source)
    return "config_check_failed" if "claudeai" in sources else None
