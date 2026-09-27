"""Per-Gent keys: each spawned Gent authenticates as gent-<stack_id> with the
local-only "gent" policy; keys appear and disappear without a gateway restart."""
from __future__ import annotations

import os
import time

import pytest

from conftest import GENT_KEYS, KEYS
from test_kent_gateway import auth, denied

SID = "1a2b3c4d"


async def test_gent_key_maps_to_its_own_identity(gw):
    r = await auth(gw, GENT_KEYS[SID], body={"model": "fast", "messages": []})
    assert r.key_alias == f"gent-{SID}" and r.user_id == f"gent-{SID}"
    assert r.models == ["fast", "router"]


@pytest.mark.parametrize("model", ["smart", "frontier", "auto", "anthropic/claude-opus-5-5", "FAST"])
async def test_gent_key_cannot_reach_other_tiers(gw, model):
    await denied(gw, GENT_KEYS[SID], 403, body={"model": model, "messages": []})


async def test_gent_key_restricted_routes_and_shape(gw):
    await denied(gw, GENT_KEYS[SID], 403, path="/key/generate", body={})
    await denied(gw, GENT_KEYS[SID], 403, body={"model": "fast", "api_base": "http://evil"})
    await denied(gw, GENT_KEYS[SID], 403, path="/metrics/", method="GET")


async def test_gents_cannot_use_each_others_identity(gw):
    a = await auth(gw, GENT_KEYS["1a2b3c4d"], body={"model": "fast"})
    b = await auth(gw, GENT_KEYS["deadbeef"], body={"model": "fast"})
    assert a.user_id != b.user_id


def _touch_dir(d):
    # Force a new directory mtime even on coarse-timestamp filesystems.
    t = time.time() + 5
    os.utime(d, (t, t))


async def test_new_key_is_picked_up_and_removed_key_revoked(gw):
    d = gw._GENT_KEYS_DIR
    key = "sk-kent-gent3-" + "9" * 40
    await denied(gw, key, 401, body={"model": "fast"})
    (d / "0badc0de.key").write_text(key)
    _touch_dir(d)
    assert (await auth(gw, key, body={"model": "fast"})).user_id == "gent-0badc0de"
    (d / "0badc0de.key").unlink()
    _touch_dir(d)
    await denied(gw, key, 401, body={"model": "fast"})


@pytest.mark.parametrize("name", ["ABCDEF12.key", "1234567.key", "123456789.key", "operator.key",
                                  "../kent.key", "1a2b3c4d.key.bak", "zzzzzzzz.key"])
async def test_malformed_key_filenames_are_ignored(gw, name):
    d = gw._GENT_KEYS_DIR
    key = "sk-kent-rogue-" + "7" * 40
    target = d / name
    if "/" in name:
        return  # a path separator cannot be part of a file name in the dir
    target.write_text(key)
    _touch_dir(d)
    try:
        await denied(gw, key, 401, body={"model": "fast"})
    finally:
        target.unlink()
        _touch_dir(d)


async def test_empty_gent_key_file_grants_nothing(gw):
    d = gw._GENT_KEYS_DIR
    (d / "00000000.key").write_text("\n")
    _touch_dir(d)
    try:
        await denied(gw, "", 401, body={"model": "fast"})
    finally:
        (d / "00000000.key").unlink()
        _touch_dir(d)


async def test_base_identities_unaffected(gw):
    assert (await auth(gw, KEYS["kent"], body={"model": "frontier"})).user_id == "kent"
