"""What a plugin author writes against.

    # plugins/weather/plugin.py
    from typing import Annotated
    from pydantic import Field
    from app.plugins.sdk import tool

    DESCRIPTION = "Weather lookups"

    @tool
    async def forecast(city: Annotated[str, Field(description="City name")], days: int = 1) -> str:
        '''Weather forecast for a city.'''
        ...

The function's docstring is the description the model sees; its type hints
become the tool's JSON schema, and arguments from the model are validated
against them before the function runs. Return a string, or anything
JSON-serialisable. Raise an exception to report an error to the model.
Functions may be sync or async.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from app.llm import inline_schema

_TOOL_MARK = "__ai_platform_tool__"


@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable[..., Any]
    args_model: type[BaseModel]

    def parameters(self) -> dict:
        schema = inline_schema(self.args_model.model_json_schema())
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return schema


def tool(func: Callable[..., Any] | None = None, *, name: str | None = None, description: str | None = None):
    """Mark a function as a tool. Usable as @tool or @tool(name=..., description=...)."""

    def mark(f: Callable[..., Any]) -> Callable[..., Any]:
        setattr(f, _TOOL_MARK, {"name": name or f.__name__, "description": description})
        return f

    return mark(func) if func is not None else mark


def spec_for(func: Callable[..., Any]) -> ToolSpec:
    meta = getattr(func, _TOOL_MARK)
    hints = inspect.get_annotations(func, eval_str=True)
    fields: dict[str, Any] = {}
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"tool {func.__name__}: *args/**kwargs are not supported")
        annotation = hints.get(param.name, str)
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (annotation, default)
    args_model = create_model(
        f"{func.__name__}_args", __config__=ConfigDict(extra="forbid"), **fields,
    )
    description = meta["description"] or inspect.getdoc(func) or meta["name"]
    return ToolSpec(name=meta["name"], description=description.strip(), func=func, args_model=args_model)


def is_tool(obj: Any) -> bool:
    return callable(obj) and hasattr(obj, _TOOL_MARK)
