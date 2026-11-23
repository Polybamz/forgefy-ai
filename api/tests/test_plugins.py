"""The plugin system and the plugins shipped with the platform."""
from __future__ import annotations

import asyncio
import textwrap

import pytest

from app.plugins import call_tool, load_plugin, load_plugins
from tests.conftest import PROJECT


def test_shipped_plugins_load_and_the_template_is_skipped(project_plugins):
    assert set(project_plugins) == {"calculator", "clock", "web"}


def test_schema_comes_from_type_hints(tmp_path):
    # Copied to a loadable name, as an author would.
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "plugin.py").write_text((PROJECT / "plugins" / "_template" / "plugin.py").read_text())
    plugin = load_plugin(tmp_path / "orders")
    params = plugin.tools["lookup_order"].parameters()
    assert params["required"] == ["order_id"]
    assert params["properties"]["order_id"]["description"].startswith("The order number")
    assert params["properties"]["include_items"]["type"] == "boolean"
    assert set(plugin.tools) == {"lookup_order", "convert_currency"}
    assert plugin.tools["convert_currency"].parameters()["properties"]["to"]["enum"] == ["XAF", "EUR", "USD"]


async def test_arguments_are_validated(project_plugins):
    calc = project_plugins["calculator"]
    assert (await call_tool(calc, "calculate", '{"expr": "1"}', 5)).startswith("error: invalid arguments")
    assert (await call_tool(calc, "calculate", "not json", 5)).startswith("error: invalid arguments")
    assert (await call_tool(calc, "nope", "{}", 5)).startswith("error:")


@pytest.mark.parametrize("expression, expected", [
    ("2 500 * 3".replace(" ", ""), "= 7500"),
    ("(100 - 20) / 4", "= 20"),
    ("sqrt(16) + round(2.6)", "= 7"),
])
async def test_calculator(project_plugins, expression, expected):
    assert (await call_tool(project_plugins["calculator"], "calculate", {"expression": expression}, 5)).endswith(expected)


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')", "open('/etc/passwd')", "(9**999)**999", "10**10**10", "a.b",
])
async def test_calculator_refuses_unsafe_or_huge(project_plugins, expression):
    result = await call_tool(project_plugins["calculator"], "calculate", {"expression": expression}, 5)
    assert result.startswith("error:")


async def test_clock(project_plugins):
    assert '"Africa/Douala"' in await call_tool(project_plugins["clock"], "now", {"timezone": "Africa/Douala"}, 5)
    assert (await call_tool(project_plugins["clock"], "now", {"timezone": "Mars/Base"}, 5)).startswith("error:")


@pytest.mark.parametrize("url", [
    "http://localhost:11434/api/tags", "http://127.0.0.1/", "http://10.0.0.5/", "http://169.254.169.254/latest",
    "file:///etc/passwd", "ftp://example.com/",
])
async def test_web_refuses_private_addresses(project_plugins, url):
    result = await call_tool(project_plugins["web"], "fetch", {"url": url}, 10)
    assert result.startswith("error:"), result


async def test_slow_tools_time_out(tmp_path):
    folder = tmp_path / "slow"
    folder.mkdir()
    (folder / "plugin.py").write_text(textwrap.dedent("""
        import asyncio
        from app.plugins.sdk import tool

        @tool
        async def wait() -> str:
            '''Waits.'''
            await asyncio.sleep(5)
            return "done"
    """))
    plugin = load_plugin(folder)
    assert "did not finish" in await call_tool(plugin, "wait", {}, 0.1)


def test_broken_plugin_is_skipped_not_fatal(tmp_path, caplog):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "plugin.py").write_text("raise RuntimeError('boom')")
    (tmp_path / "Bad-Name").mkdir()
    (tmp_path / "Bad-Name" / "plugin.py").write_text("")
    assert load_plugins(tmp_path) == {}
    assert "boom" in caplog.text


def test_check_command(capsys):
    from app.plugins.check import main

    assert main([str(PROJECT / "plugins" / "calculator"), "calculate", '{"expression": "6*7"}']) == 0
    assert "= 42" in capsys.readouterr().out
    asyncio.set_event_loop(asyncio.new_event_loop())


def test_cli_key_for_other_tools_gets_no_agents_scope(capsys):
    from app import cli
    from app.store import MemoryStore

    store = MemoryStore()
    asyncio.run(cli.main(["keys", "create", "--app", "n8n", "--env", "production", "--plugin", "web"], store))
    [key] = asyncio.run(store.list_keys())
    assert key.scopes == ["chat", "skills", "plugin:web"]
