"""Tamper tests for install/services/kent-core/bin/kent_audit.py."""
import importlib.util
import threading
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "kent_audit", Path(__file__).resolve().parents[2] / "install/services/kent-core/bin/kent_audit.py")
ka = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ka)
SECRET = b"test-secret-" + b"x" * 32


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "audit" / "chain.log"
    for i in range(10):
        ka.append(p, SECRET, "tester", "event", f"detail {i}")
    return p


def test_clean_chain_verifies(log):
    assert ka.verify(log, SECRET) == []


def test_empty_or_missing_chain_is_valid(tmp_path):
    assert ka.verify(tmp_path / "none.log", SECRET) == []


def test_file_mode_is_0600(log):
    assert oct(log.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("mutate", [
    lambda L: L[:3] + [L[3].replace("detail 3", "detail X")] + L[4:],   # edit content
    lambda L: L[:4] + L[5:],                                            # delete middle
    lambda L: L[:2] + [L[3], L[2]] + L[4:],                             # reorder
    lambda L: [L[0].replace("tester", "admin")] + L[1:],                # edit entity of first
    lambda L: L[:5] + ["2026-01-01T00:00:00.000+00:00|forged|event|x|" + "0" * 64] + L[5:],  # insert forged
    lambda L: L[1:],                                                    # drop genesis entry
])
def test_tampering_detected(log, mutate):
    lines = log.read_text().splitlines()
    log.write_text("\n".join(mutate(lines)) + "\n")
    assert ka.verify(log, SECRET), "tampering went undetected"


def test_truncate_then_append_detected(log):
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:4]) + "\n")
    ka.append(log, SECRET, "attacker", "cover-up", "")
    assert ka.verify(log, SECRET) == []  # a valid chain again...
    # ...but truncation is only detectable against an external anchor: the count
    # and last HMAC recorded elsewhere (the digest records both each day).
    assert len(log.read_text().splitlines()) == 5


def test_anchor_detects_truncate_then_append(log):
    anchor = (10, log.read_text().splitlines()[9].rsplit("|", 1)[1])
    assert ka.check_anchor(log, anchor) == []
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:4]) + "\n")
    ka.append(log, SECRET, "attacker", "cover-up", "")
    assert ka.verify(log, SECRET) == []                   # chain alone looks fine...
    assert ka.check_anchor(log, anchor)                   # ...the anchor catches it


def test_anchor_detects_rewritten_prefix_of_same_length(log):
    anchor = (10, log.read_text().splitlines()[9].rsplit("|", 1)[1])
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:9]) + "\n")
    ka.append(log, SECRET, "attacker", "replacement", "")  # 10 entries again, different last
    assert ka.check_anchor(log, anchor)


def test_wrong_secret_fails(log):
    assert ka.verify(log, b"other-secret")


def test_field_injection_is_sanitised(tmp_path):
    p = tmp_path / "c.log"
    ka.append(p, SECRET, "ent|ity", "ev\nent", "a|b|c\nfake|line|x|y|" + "0" * 64)
    lines = p.read_text().splitlines()
    assert len(lines) == 1 and lines[0].count("|") == 4
    assert ka.verify(p, SECRET) == []


def test_concurrent_appends_keep_chain_valid(tmp_path):
    p = tmp_path / "c.log"
    ts = [threading.Thread(target=lambda i=i: [ka.append(p, SECRET, f"t{i}", "e", str(j)) for j in range(25)])
          for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(p.read_text().splitlines()) == 200
    assert ka.verify(p, SECRET) == []



def test_anchor_lookup_ignores_alert_lines_and_other_chains():
    anchors = [ka.parse_anchor(l) for l in [
        "chain=aaaa count=5 last=h5",
        "CHAIN INVALID problems=3",                 # alert line under the same tag
        "chain=bbbb count=2 last=x2",               # a later, different chain
        "count=9 last=legacy9",                     # pre-chain-id anchor
    ]]
    anchors = [a for a in anchors if a]
    assert ka.latest_anchor("aaaa", anchors) == (5, "h5")
    assert ka.latest_anchor("bbbb", anchors) == (2, "x2")
    assert ka.latest_anchor("cccc", anchors) is None


def test_chain_id_depends_on_secret_only():
    assert ka.chain_id(b"s1") == ka.chain_id(b"s1") != ka.chain_id(b"s2")
    assert len(ka.chain_id(b"s1")) == 16
