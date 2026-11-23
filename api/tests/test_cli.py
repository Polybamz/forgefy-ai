from __future__ import annotations

from app import cli
from app.auth import hash_key
from app.store import MemoryStore


async def test_create_list_revoke(capsys):
    store = MemoryStore()
    assert await cli.main(["keys", "create", "--app", "vndor", "--env", "staging", "--rate", "10"], store) == 0
    out = capsys.readouterr().out
    key = next(line.strip() for line in out.splitlines() if line.strip().startswith("aik_") and "…" not in line)

    found = await store.find_key(hash_key(key))
    assert found and found.app == "vndor" and found.rate_limit_per_minute == 10

    await cli.main(["keys", "list"], store)
    assert key[:12] in capsys.readouterr().out
    assert key not in str(await store.list_keys())  # only the hash is kept

    assert await cli.main(["keys", "revoke", key[:12]], store) == 0
    assert (await store.find_key(hash_key(key))).revoked
    assert await cli.main(["keys", "revoke", key[:12]], store) == 1


def test_rate_limiter_window():
    from app.auth import RateLimiter

    rl = RateLimiter()
    assert rl.check(1, 2, now=0) == 0
    assert rl.check(1, 2, now=1) == 0
    assert rl.check(1, 2, now=2) == 59
    assert rl.check(1, 2, now=60.5) == 0  # the first hit left the window
