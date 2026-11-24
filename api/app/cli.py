"""Manage API keys and read usage, from the server:

    docker compose exec api python -m app.cli keys create --app forgefy --env production
    docker compose exec api python -m app.cli keys create --app acme-corp --env production --rate 60
    docker compose exec api python -m app.cli keys list
    docker compose exec api python -m app.cli keys revoke aik_AbCdEfGh
    docker compose exec api python -m app.cli usage --days 30

--app names who the key is for — one of your apps, a customer, a script — so
usage can be reported per app.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

from app.auth import new_key
from app.config import get_settings
from app.store import ENVIRONMENTS, Store, make_store

_APP_NAME = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


def _app_name(value: str) -> str:
    if not _APP_NAME.match(value):
        raise argparse.ArgumentTypeError("2-32 characters: lowercase letters, digits and '-', starting with a letter")
    return value


async def _create(store: Store, args: argparse.Namespace) -> int:
    key, prefix, key_hash = new_key()
    record = await store.create_key(
        app=args.app, environment=args.env, name=args.name or f"{args.app}-{args.env}",
        prefix=prefix, key_hash=key_hash,
        rate_limit_per_minute=args.rate or get_settings().DEFAULT_RATE_LIMIT_PER_MINUTE,
    )
    print(f"Created key {record.prefix}… for {record.app} ({record.environment}), "
          f"{record.rate_limit_per_minute} requests/minute.")
    print("\nCopy it now — it is not stored and cannot be shown again:\n")
    print(f"  {key}\n")
    return 0


async def _list(store: Store, args: argparse.Namespace) -> int:
    keys = await store.list_keys()
    if not keys:
        print("No keys yet. Create one: python -m app.cli keys create --app forgefy --env production")
        return 0
    print(f"{'PREFIX':<14}{'APP':<16}{'ENV':<13}{'RATE/MIN':<10}{'LAST USED':<22}STATUS")
    for k in keys:
        last = k.last_used_at.strftime("%Y-%m-%d %H:%M UTC") if k.last_used_at else "never"
        status = "revoked" if k.revoked else "active"
        print(f"{k.prefix:<14}{k.app[:15]:<16}{k.environment:<13}{k.rate_limit_per_minute:<10}{last:<22}{status}")
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
    print(f"{'APP':<16}{'API':<14}{'STATUS':<14}{'REQUESTS':>9}{'AVG MS':>9}{'P95 MS':>9}{'TOKENS IN':>11}{'TOKENS OUT':>12}")
    for r in rows:
        print(f"{r['app'][:15]:<16}{r['task']:<14}{r['status']:<14}{r['requests']:>9}{r['avg_latency_ms']:>9}"
              f"{r['p95_latency_ms']:>9}{r['prompt_tokens']:>11}{r['completion_tokens']:>12}")
    return 0


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    keys = sub.add_parser("keys", help="create, list and revoke API keys").add_subparsers(dest="action", required=True)
    create = keys.add_parser("create")
    create.add_argument("--app", required=True, type=_app_name, help="who the key is for: an app, customer or tool")
    create.add_argument("--env", required=True, choices=ENVIRONMENTS)
    create.add_argument("--name")
    create.add_argument("--rate", type=int, help="requests per minute (default DEFAULT_RATE_LIMIT_PER_MINUTE)")
    create.set_defaults(run=_create)
    keys.add_parser("list").set_defaults(run=_list)
    revoke = keys.add_parser("revoke")
    revoke.add_argument("prefix", help="the key's prefix, as shown by: keys list")
    revoke.set_defaults(run=_revoke)

    usage = sub.add_parser("usage", help="requests, latency and tokens per app")
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
