"""Manage API keys and read usage, from the server:

    docker compose exec api python -m app.cli keys create --app forgefy --env production
    docker compose exec api python -m app.cli keys create --app n8n --env production --plugin web
    docker compose exec api python -m app.cli keys list
    docker compose exec api python -m app.cli keys scopes aik_AbCdEfGh chat skills plugin:web
    docker compose exec api python -m app.cli keys revoke aik_AbCdEfGh
    docker compose exec api python -m app.cli usage --days 30

--app is forgefy or vndor for those products' agents, or any other short name
(a script, n8n, an editor…) for a key that uses the general /v1 API.
Scopes: agents, chat, skills, plugin:<name>, plugin:* (all plugins).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

from app.auth import new_key
from app.config import get_settings
from app.store import APPS, DEFAULT_SCOPES, ENVIRONMENTS, Store, make_store

_APP_NAME = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_SCOPE = re.compile(r"^(agents|chat|skills|plugin:(\*|[a-z][a-z0-9_]{0,31}))$")


def _app_name(value: str) -> str:
    if not _APP_NAME.match(value):
        raise argparse.ArgumentTypeError("2-32 characters: lowercase letters, digits and '-', starting with a letter")
    return value


def _scope(value: str) -> str:
    if not _SCOPE.match(value):
        raise argparse.ArgumentTypeError("one of: agents, chat, skills, plugin:<name>, plugin:*")
    return value


async def _create(store: Store, args: argparse.Namespace) -> int:
    scopes = list(args.scopes or DEFAULT_SCOPES)
    if args.app not in APPS and "agents" in scopes:
        scopes.remove("agents")  # only forgefy and vndor have agents
    scopes += [f"plugin:{p}" for p in args.plugin or [] if f"plugin:{p}" not in scopes]

    key, prefix, key_hash = new_key()
    record = await store.create_key(
        app=args.app, environment=args.env, name=args.name or f"{args.app}-{args.env}",
        prefix=prefix, key_hash=key_hash,
        rate_limit_per_minute=args.rate or get_settings().DEFAULT_RATE_LIMIT_PER_MINUTE,
        scopes=scopes,
    )
    print(f"Created key {record.prefix}… for {record.app} ({record.environment}), "
          f"{record.rate_limit_per_minute} requests/minute, scopes: {' '.join(record.scopes)}.")
    print("\nCopy it now — it is not stored and cannot be shown again:\n")
    print(f"  {key}\n")
    return 0


async def _scopes(store: Store, args: argparse.Namespace) -> int:
    if await store.set_scopes(args.prefix, args.scopes):
        print(f"{args.prefix} scopes: {' '.join(args.scopes)}")
        return 0
    print(f"No key with prefix {args.prefix} (see: keys list).", file=sys.stderr)
    return 1


async def _list(store: Store, args: argparse.Namespace) -> int:
    keys = await store.list_keys()
    if not keys:
        print("No keys yet. Create one: python -m app.cli keys create --app forgefy --env production")
        return 0
    print(f"{'PREFIX':<14}{'APP':<12}{'ENV':<13}{'RATE/MIN':<10}{'LAST USED':<22}{'STATUS':<9}SCOPES")
    for k in keys:
        last = k.last_used_at.strftime("%Y-%m-%d %H:%M UTC") if k.last_used_at else "never"
        status = "revoked" if k.revoked else "active"
        print(f"{k.prefix:<14}{k.app[:11]:<12}{k.environment:<13}{k.rate_limit_per_minute:<10}{last:<22}"
              f"{status:<9}{' '.join(k.scopes)}")
    return 0


async def _revoke(store: Store, args: argparse.Namespace) -> int:
    if await store.revoke_key(args.prefix):
        print(f"Revoked {args.prefix}. Requests with it now get 401.")
        return 0
    print(f"No active key with prefix {args.prefix} (see: keys list).", file=sys.stderr)
    return 1


async def _usage(store: Store, args: argparse.Namespace) -> int:
    rows = await store.usage_summary(args.app, args.days)
    if not rows:
        print(f"No requests in the last {args.days} days.")
        return 0
    print(f"{'APP':<10}{'TASK':<22}{'STATUS':<16}{'REQUESTS':>9}{'AVG MS':>9}{'P95 MS':>9}{'TOKENS IN':>11}{'TOKENS OUT':>12}")
    for r in rows:
        print(f"{r['app']:<10}{r['task']:<22}{r['status']:<16}{r['requests']:>9}{r['avg_latency_ms']:>9}"
              f"{r['p95_latency_ms']:>9}{r['prompt_tokens']:>11}{r['completion_tokens']:>12}")
    return 0


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    keys = sub.add_parser("keys", help="create, list and revoke API keys").add_subparsers(dest="action", required=True)
    create = keys.add_parser("create")
    create.add_argument("--app", required=True, type=_app_name,
                        help="forgefy or vndor for their agents, or any short name for general /v1 use")
    create.add_argument("--env", required=True, choices=ENVIRONMENTS)
    create.add_argument("--name")
    create.add_argument("--rate", type=int, help="requests per minute (default DEFAULT_RATE_LIMIT_PER_MINUTE)")
    create.add_argument("--scopes", nargs="+", type=_scope,
                        help=f"replace the default scopes ({' '.join(DEFAULT_SCOPES)})")
    create.add_argument("--plugin", action="append", metavar="NAME",
                        help="grant a plugin (repeatable); '*' for all plugins")
    create.set_defaults(run=_create)
    keys.add_parser("list").set_defaults(run=_list)
    scopes = keys.add_parser("scopes", help="set a key's scopes (replaces them)")
    scopes.add_argument("prefix")
    scopes.add_argument("scopes", nargs="+", type=_scope)
    scopes.set_defaults(run=_scopes)
    revoke = keys.add_parser("revoke")
    revoke.add_argument("prefix", help="the key's prefix, as shown by: keys list")
    revoke.set_defaults(run=_revoke)

    usage = sub.add_parser("usage", help="requests, latency and tokens per app and task")
    usage.add_argument("--days", type=int, default=7)
    usage.add_argument("--app", type=_app_name)
    usage.set_defaults(run=_usage)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None, store: Store | None = None) -> int:
    args = parse(argv)
    store = store or make_store(get_settings())
    await store.init()
    try:
        return await args.run(store, args)
    finally:
        await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
