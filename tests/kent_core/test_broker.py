"""Tests for install/services/kent-core/libexec/kent-broker and kent-broker-client
(docs/design/kent-sandbox.md §4): the allowlist, project staging, and a client↔broker round trip."""
import importlib.machinery
import importlib.util
import json
import os
import socket
import threading
from pathlib import Path

import pytest

LIBEXEC = Path(__file__).resolve().parents[2] / "install/services/kent-core/libexec"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(LIBEXEC / name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


@pytest.fixture
def kb(tmp_path, monkeypatch):
    b = load("kent-broker")
    monkeypatch.setattr(b, "WORK", tmp_path / "work")
    monkeypatch.setattr(b, "STAGE", tmp_path / "stage")
    monkeypatch.setattr(b, "KHOME", tmp_path)
    (tmp_path / "work").mkdir()
    b.audited = []
    monkeypatch.setattr(b, "audit", lambda ev, d: b.audited.append((ev, d)))
    return b


def project(d: Path, **override) -> Path:
    d.mkdir(parents=True)
    for f in ("project.yaml", "agents.yaml", "tasks.yaml"):
        (d / f).write_text(override.get(f, f"# {f}\n"))
    return d


@pytest.mark.parametrize("tool,argv,want", [
    ("kent-gent", [], ["list"]),
    ("kent-gent", ["list"], ["list"]),
    ("kent-gent", ["status", "0a1b2c3d"], ["status", "0a1b2c3d"]),
    ("kent_gent.py", ["logs", "0a1b2c3d", "--tail", "50"], ["logs", "0a1b2c3d", "--tail", "50"]),
    ("kent-gent", ["wait", "0a1b2c3d", "--timeout", "600"], ["wait", "0a1b2c3d", "--timeout", "600"]),
    ("kent-gent", ["assess", "0a1b2c3d", "--no-publish"], ["assess", "0a1b2c3d", "--no-publish"]),
    ("kent-gent", ["destroy", "0a1b2c3d", "--purge"], ["destroy", "0a1b2c3d", "--purge"]),
    ("kent-audit", ["verify"], ["verify"]),
    ("kent-audit", ["append", "note", "checked", "gent", "x"], ["append", "kent", "note", "checked gent x"]),
    ("kent-digest", [], []),
    ("kent-qa-audit", [], []),
    ("kent-poll-learnings", [], []),
])
def test_allowed(kb, tool, argv, want):
    _, args, _, staged = kb.validate({"tool": tool, "argv": argv})
    assert args == want and staged is None


@pytest.mark.parametrize("req", [
    None, [], {"tool": "kent-gent", "argv": [], "cwd": "/"},              # malformed / extra keys
    {"tool": "bash", "argv": ["-c", "id"]}, {"tool": "../bin/kent-gent"},  # not a brokered tool
    {"tool": "kent", "argv": ["chat"]}, {"tool": "kent-exec"},
    {"tool": "kent-gent", "argv": ["status", "../../etc"]},               # bad id
    {"tool": "kent-gent", "argv": ["status", "0a1b2c3d", "--purge"]},     # option of another action
    {"tool": "kent-gent", "argv": ["logs", "0a1b2c3d", "--tail", "-1"]},
    {"tool": "kent-gent", "argv": ["wait", "0a1b2c3d", "--timeout", "999999"]},
    {"tool": "kent-gent", "argv": ["destroy", "0a1b2c3d", "--purge", "x"]},
    {"tool": "kent-gent", "argv": ["spawn", "--name", "a;b", "--project", "/x"]},
    {"tool": "kent-audit", "argv": ["append"]},
    {"tool": "kent-audit", "argv": ["append", "Bad Event"]},
    {"tool": "kent-audit", "argv": ["append", "note", "x" * 1001]},
    {"tool": "kent-audit", "argv": ["anchor"]},
    {"tool": "kent-digest", "argv": ["--out", "/tmp/x"]},
    {"tool": "kent-gent", "argv": ["list"], "stdin": "aGk="},              # stdin only for the hook
    {"tool": "kent_notices_hook.py", "argv": ["x"]},
    {"tool": "kent-gent", "argv": ["x"] * 33},
])
def test_refused(kb, req):
    with pytest.raises(kb.Refused):
        kb.validate(req)


def test_audit_append_cannot_choose_entity(kb):
    _, args, _, _ = kb.validate({"tool": "kent-audit", "argv": ["append", "note", "human:alice", "approved"]})
    assert args[:2] == ["append", "kent"]


def test_hook_passes_stdin(kb):
    import base64
    script, args, stdin, _ = kb.validate({"tool": "kent_notices_hook.py", "stdin": base64.b64encode(b'{"s":1}').decode()})
    assert (script, args, stdin) == ("kent_notices_hook.py", [], b'{"s":1}')


def test_spawn_stages_a_copy_outside_work(kb, tmp_path):
    src = project(tmp_path / "work" / "p1")
    _, args, _, staged = kb.validate({"tool": "kent-gent", "argv": ["spawn", "--name", "demo", "--project", str(src)]})
    assert args == ["spawn", "--name", "demo", "--project", str(staged)]
    assert staged.parent == tmp_path / "stage" and oct(staged.stat().st_mode & 0o777) == "0o700"
    assert sorted(p.name for p in staged.iterdir()) == ["agents.yaml", "project.yaml", "tasks.yaml"]


@pytest.mark.parametrize("where", ["outside", "work_itself", "missing"])
def test_spawn_project_must_be_under_work(kb, tmp_path, where):
    d = {"outside": project(tmp_path / "elsewhere"), "work_itself": tmp_path / "work",
         "missing": tmp_path / "work" / "nope"}[where]
    with pytest.raises(kb.Refused):
        kb.validate({"tool": "kent-gent", "argv": ["spawn", "--name", "d", "--project", str(d)]})


def test_spawn_refuses_symlinked_file(kb, tmp_path):
    secret = tmp_path / "kent.db"; secret.write_text("SECRET")
    src = project(tmp_path / "work" / "p2")
    (src / "tasks.yaml").unlink(); (src / "tasks.yaml").symlink_to(secret)
    with pytest.raises(kb.Refused):
        kb.validate({"tool": "kent-gent", "argv": ["spawn", "--name", "d", "--project", str(src)]})
    assert not any((tmp_path / "stage").iterdir())   # partial copy removed


def test_spawn_refuses_symlinked_dir_out_of_work(kb, tmp_path):
    outside = project(tmp_path / "outside")
    (tmp_path / "work" / "link").symlink_to(outside)
    with pytest.raises(kb.Refused):
        kb.validate({"tool": "kent-gent", "argv": ["spawn", "--name", "d", "--project", str(tmp_path / "work" / "link")]})


def test_spawn_refuses_oversize_and_fifo(kb, tmp_path):
    big = project(tmp_path / "work" / "big", **{"agents.yaml": "x" * (256 * 1024 + 1)})
    with pytest.raises(kb.Refused):
        kb.validate({"tool": "kent-gent", "argv": ["spawn", "--name", "d", "--project", str(big)]})
    fifo = project(tmp_path / "work" / "fifo")
    (fifo / "project.yaml").unlink(); os.mkfifo(fifo / "project.yaml")
    with pytest.raises(kb.Refused):   # must not block on open
        kb.validate({"tool": "kent-gent", "argv": ["spawn", "--name", "d", "--project", str(fifo)]})


@pytest.fixture
def fake_bin(kb, tmp_path, monkeypatch):
    """Stand-in tools that echo what they were run with."""
    b = tmp_path / "bin"; b.mkdir()
    for script in set(kb.TOOLS.values()):
        (b / script).write_text("import os, sys\nprint('ran', os.path.basename(sys.argv[0]), sys.argv[1:], "
                                "sys.stdin.read(), os.environ.get('KENT_CONF'))\nsys.exit(3)\n")
    monkeypatch.setattr(kb, "BIN", b)
    return b


def test_handle_runs_tool_and_audits_first(kb, fake_bin):
    r = kb.handle(json.dumps({"tool": "kent-gent", "argv": ["status", "0a1b2c3d"]}).encode())
    assert r["rc"] == 3 and "ran kent_gent.py ['status', '0a1b2c3d']  /etc/kent/kent/kent.conf" in r["stdout"]
    assert kb.audited == [("broker", "kent_gent.py status 0a1b2c3d")]


def test_handle_refusal_is_audited_and_not_run(kb, fake_bin):
    r = kb.handle(json.dumps({"tool": "kent-gent", "argv": ["status", "nothex!!"]}).encode())
    assert r["rc"] == 2 and r["stdout"] == "" and "kent-broker:" in r["stderr"]
    assert kb.audited and kb.audited[0][0] == "broker_refused"
    assert kb.handle(b"not json")["rc"] == 2


def test_hook_not_audited(kb, fake_bin):
    kb.handle(json.dumps({"tool": "kent_notices_hook.py", "stdin": ""}).encode())
    assert kb.audited == []


def test_staged_dir_removed_after_run(kb, fake_bin, tmp_path):
    src = project(tmp_path / "work" / "p3")
    r = kb.handle(json.dumps({"tool": "kent-gent", "argv": ["spawn", "--name", "d", "--project", str(src)]}).encode())
    assert "ran kent_gent.py ['spawn'" in r["stdout"]
    assert not any((tmp_path / "stage").iterdir())


def test_output_capped(kb, fake_bin, monkeypatch):
    monkeypatch.setattr(kb, "MAX_OUT", 5)
    assert kb.handle(json.dumps({"tool": "kent-digest"}).encode())["stdout"].endswith("[kent-broker: output truncated]")


def serve_once(kb, path: Path):
    """A one-connection broker on a Unix socket, through the real main()'s read/reply path."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(str(path)); srv.listen(1)

    def run():
        conn, _ = srv.accept()
        saved = os.dup(0)
        try:
            os.dup2(conn.fileno(), 0)
            kb.main()
        finally:
            os.dup2(saved, 0); os.close(saved); conn.close(); srv.close()
    t = threading.Thread(target=run); t.start()
    return t


@pytest.mark.parametrize("name,argv,rc,out", [
    ("kent-gent", ["list"], 3, "ran kent_gent.py ['list']"),
    ("kent-gent", ["rm", "-rf", "/"], 2, ""),
])
def test_client_round_trip(kb, fake_bin, tmp_path, monkeypatch, capsys, name, argv, rc, out):
    sock = tmp_path / "b.sock"
    t = serve_once(kb, sock)
    client = load("kent-broker-client")
    monkeypatch.setattr(client, "SOCK", str(sock))
    monkeypatch.setattr("sys.argv", [f"/opt/kent-core/bin/{name}", *argv])
    assert client.main() == rc
    t.join(5)
    assert out in capsys.readouterr().out


def test_client_without_broker(tmp_path, monkeypatch, capsys):
    client = load("kent-broker-client")
    monkeypatch.setattr(client, "SOCK", str(tmp_path / "none.sock"))
    monkeypatch.setattr("sys.argv", ["/opt/kent-core/bin/kent-gent", "list"])
    assert client.main() == 1 and "broker is not available" in capsys.readouterr().err
    monkeypatch.setattr("sys.argv", ["/opt/kent-core/bin/kent_notices_hook.py"])
    monkeypatch.setattr("sys.stdin", open(os.devnull))
    assert client.main() == 0   # the hook never breaks a chat
