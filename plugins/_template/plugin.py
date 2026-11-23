"""Template — copy this folder to plugins/<your_plugin>/ (lowercase, no leading _).

Folders starting with "_" are not loaded, so this one stays inert.

Each @tool function becomes a tool named "<folder>__<function>". The docstring
is what the model reads to decide when to call it — say what it does and when
to use it. Type hints (with Field descriptions) become the JSON schema, and
arguments are validated against them before your function runs.

Return a string, or anything JSON-serialisable. Raise an exception for errors:
the message goes back to the model so it can correct itself. Keep results
short — they go into the model's context. Calls time out after
TOOL_TIMEOUT_SECONDS.

Check it before restarting the API:
    docker compose exec api python -m app.plugins.check plugins/<your_plugin>
    docker compose exec api python -m app.plugins.check plugins/<your_plugin> <tool> '{"arg": "value"}'
"""
from typing import Annotated, Literal

from pydantic import Field

from app.plugins.sdk import tool

DESCRIPTION = "What this plugin is for, in one line."


@tool
def lookup_order(
    order_id: Annotated[str, Field(description="The order number, e.g. VN-1042")],
    include_items: bool = False,
) -> dict:
    """Look up an order's status. Use when someone asks where their order is."""
    # Call your own systems here (database, internal API…). Read secrets from
    # environment variables — never put them in the code or the result.
    return {"order_id": order_id, "status": "shipped", "items": [] if include_items else None}


@tool(name="convert_currency")
async def convert(
    amount: float,
    to: Literal["XAF", "EUR", "USD"],
) -> str:
    """Async tools work the same way — use them for network calls."""
    return f"{amount} → {to}: (implement me)"
