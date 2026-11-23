"""Check a plugin before installing it, and optionally call one of its tools.

    python -m app.plugins.check plugins/my_plugin
    python -m app.plugins.check plugins/my_plugin my_tool '{"arg": "value"}'
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.plugins import call_tool, load_plugin, openai_tools


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    try:
        plugin = load_plugin(Path(argv[0]))
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    print(f"ok  plugin '{plugin.name}': {plugin.description or '(no DESCRIPTION)'}")
    for t in openai_tools(plugin):
        fn = t["function"]
        print(f"\n  {fn['name']}\n    {fn['description']}\n    parameters: {json.dumps(fn['parameters'])}")

    if len(argv) >= 2:
        arguments = argv[2] if len(argv) > 2 else "{}"
        result = asyncio.run(call_tool(plugin, argv[1], arguments, timeout=60))
        print(f"\n{argv[1]}({arguments}) →\n{result}")
        return 1 if result.startswith("error:") else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
