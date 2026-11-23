"""Server-side plugins: Python tools the model can call, run by the platform.

Each plugin is a folder in PLUGINS_DIR with a plugin.py; its @tool functions
become tools named "<plugin>__<function>". Folders starting with "_" or "."
are skipped (templates, drafts).

Plugins run inside the API process with its permissions. Install only code
you have read — callers can use a plugin, but only an administrator can add
one, and each API key must be granted each plugin.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.plugins.sdk import ToolSpec, is_tool, spec_for

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SEPARATOR = "__"
MAX_RESULT_CHARS = 20_000


@dataclass
class Plugin:
    name: str
    description: str
    tools: dict[str, ToolSpec] = field(default_factory=dict)


class PluginError(Exception):
    pass


def load_plugins(directory: str | Path) -> dict[str, Plugin]:
    """Import every plugin in the directory. A broken plugin is logged and skipped."""
    root = Path(directory)
    plugins: dict[str, Plugin] = {}
    if not root.is_dir():
        return plugins
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith(("_", ".")):
            continue
        try:
            plugin = load_plugin(folder)
        except Exception as exc:
            logger.error("plugin %s not loaded: %s", folder.name, exc)
            continue
        plugins[plugin.name] = plugin
        logger.info("plugin %s loaded: %s", plugin.name, ", ".join(plugin.tools))
    return plugins


def load_plugin(folder: Path) -> Plugin:
    name = folder.name
    if not _NAME.match(name):
        raise PluginError(f"folder name must match {_NAME.pattern}")
    entry = folder / "plugin.py"
    if not entry.is_file():
        raise PluginError("missing plugin.py")
    module_name = f"ai_platform_plugins.{name}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    plugin = Plugin(name=name, description=str(getattr(module, "DESCRIPTION", "")).strip())
    for _, obj in inspect.getmembers(module, is_tool):
        tool = spec_for(obj)
        if not _NAME.match(tool.name):
            raise PluginError(f"tool name {tool.name!r} must match {_NAME.pattern}")
        plugin.tools[tool.name] = tool
    if not plugin.tools:
        raise PluginError("no @tool functions")
    return plugin


def openai_tools(plugin: Plugin) -> list[dict]:
    """The plugin's tools in OpenAI's function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": f"{plugin.name}{SEPARATOR}{tool.name}",
                "description": tool.description,
                "parameters": tool.parameters(),
            },
        }
        for tool in plugin.tools.values()
    ]


async def call_tool(plugin: Plugin, tool_name: str, arguments: str | dict, timeout: float) -> str:
    """Run one tool call and return its result as text for the model.

    Never raises: bad arguments, errors and timeouts come back as text, so the
    model can see what went wrong and correct itself.
    """
    tool = plugin.tools.get(tool_name)
    if tool is None:
        return f"error: {plugin.name} has no tool {tool_name!r}"
    try:
        raw = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
        args = tool.args_model.model_validate(raw).model_dump()
    except (json.JSONDecodeError, ValidationError) as exc:
        return f"error: invalid arguments for {tool_name}: {exc}"
    try:
        if inspect.iscoroutinefunction(tool.func):
            result = await asyncio.wait_for(tool.func(**args), timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(tool.func, **args), timeout)
    except TimeoutError:
        return f"error: {tool_name} did not finish within {timeout:.0f}s"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n… [truncated, {len(text) - MAX_RESULT_CHARS} more characters]"
    return text
