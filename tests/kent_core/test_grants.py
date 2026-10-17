"""Tests for sandbox grants (docs/design/kent-sandbox.md §5): kent-exec's checks on the folder
descriptors the `kent` command hands over, and the `kent` command's side (open, renumber, sudo -C)."""
import getpass
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = 60   # tests use descriptors 60.. so pytest's own low descriptors are never touched


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


@pytest.fixture
def ke(monkeypatch):
    saved = dict(os.environ)
    sys.path.insert(0, str(ROOT / "install/services/kent-core/bin"))
    mod = load("kent_exec", "install/services/kent-core/libexec/kent-exec")
    os.environ.clear(); os.environ.update(saved)            # kent-exec sets Kent's environment on import
    monkeypatch.setenv("SUDO_USER", getpass.getuser())
    monkeypatch.setattr(mod, "GRANT_FDS", range(BASE, BASE + 8))
    mod.audited = []
    monkeypatch.setattr(mod, "audit", lambda ev, d="": mod.audited.append((ev, d)))
    yield mod
    for fd in range(BASE, BASE + 10):
        try:
            os.close(fd)
        except OSError:
            pass


def hand(path, slot=0, flags=os.O_PATH | os.O_DIRECTORY) -> str:
    """Open path as the kent command would and place it at descriptor BASE+slot."""
    fd = os.open(path, flags)
    os.dup2(fd, BASE + slot); os.close(fd)
    return str(BASE + slot)


def test_no_grants(ke):
    assert ke.take_grants([]) == ([], [])
    assert ke.take_grants(["chat"]) == ([], ["chat"])


def test_ro_and_rw(ke, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"; a.mkdir(); b.mkdir()
    out, rest = ke.take_grants(["--grant", hand(a, 0), "--grant-rw", hand(b, 1), "x"])
    assert out == ["--grant", str(BASE), str(a), "--grant-rw", str(BASE + 1), str(b)] and rest == ["x"]
    assert ke.audited == [("grant", f"ro {a}"), ("grant", f"rw {b}")]
    assert ke.grant_fds(out) == (BASE, BASE + 1)


def test_path_comes_from_the_descriptor_not_the_caller(ke, tmp_path):
    real = tmp_path / "real"; real.mkdir()
    (tmp_path / "link").symlink_to(real)
    out, _ = ke.take_grants(["--grant", hand(tmp_path / "link")])
    assert out[2] == str(real)


def test_unreachable_parent_is_fine(ke, tmp_path):
    shut = tmp_path / "shut"; (shut / "inner").mkdir(parents=True)
    fd = hand(shut / "inner")
    shut.chmod(0o000)
    try:
        assert ke.take_grants(["--grant", fd])[0][2] == str(shut / "inner")
    finally:
        shut.chmod(0o755)


@pytest.mark.parametrize("bad", ["/", "/home", "/etc", "/etc/kent", "/usr/bin", "/proc", "/run"])
def test_refused_trees(ke, bad):
    if not os.path.isdir(bad):          # /etc/kent exists only on an installed host (not on CI runners)
        pytest.skip(f"{bad} not present on this host")
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", hand(bad)])
    assert ke.audited == []


def test_home_root_refused(ke):
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", hand(os.path.expanduser("~"))])


@pytest.mark.parametrize("arg", ["3", "2", "x", "../3", str(BASE + 8), ""])
def test_bad_or_closed_descriptor(ke, arg):
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", arg])


def test_descriptor_used_twice(ke, tmp_path):
    fd = hand(tmp_path)
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", fd, "--grant-rw", fd])


def test_file_descriptor_refused(ke, tmp_path):
    (tmp_path / "f").write_text("x")
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", hand(tmp_path / "f", flags=os.O_RDONLY)])


def test_only_own_folders(ke, tmp_path, monkeypatch):
    monkeypatch.setenv("SUDO_USER", "root")   # a different operator than the folder's owner
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", hand(tmp_path)])


def test_unknown_caller_refused(ke, tmp_path, monkeypatch):
    monkeypatch.delenv("SUDO_USER")
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant", hand(tmp_path)])


def test_rw_needs_write_access(ke, tmp_path):
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o555)
    try:
        assert ke.take_grants(["--grant", hand(d)])[0][0] == "--grant"
        with pytest.raises(SystemExit):
            ke.take_grants(["--grant-rw", hand(d, 1)])
    finally:
        d.chmod(0o755)


def test_missing_value(ke):
    with pytest.raises(SystemExit):
        ke.take_grants(["--grant"])


def test_close_other_fds_keeps_grants(ke, tmp_path):
    keep, drop = int(hand(tmp_path, 0)), int(hand(tmp_path, 1))
    import subprocess
    code = (f"import os, sys; sys.path.insert(0, {str(ROOT / 'install/services/kent-core/bin')!r}); "
            f"os.dup2(os.open({str(tmp_path)!r}, os.O_PATH), {keep}); os.dup2(os.open({str(tmp_path)!r}, os.O_PATH), {drop})\n"
            "import importlib.machinery, importlib.util\n"
            f"l = importlib.machinery.SourceFileLoader('ke', {str(ROOT / 'install/services/kent-core/libexec/kent-exec')!r})\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('ke', l)); l.exec_module(m)\n"
            f"m.close_other_fds(({keep},)); print(sorted(int(x) for x in os.listdir('/proc/self/fd')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    fds = eval(out.strip().splitlines()[-1])
    assert keep in fds and drop not in fds
    assert len(set(fds) - {0, 1, 2, keep}) <= 1   # at most listdir's own handle on /proc/self/fd


# --- the kent command --------------------------------------------------------------------------

@pytest.fixture
def kent_cmd():
    return load("kent_cmd", "install/services/kent-core/bin/kent")


def test_cli_without_grants(kent_cmd):
    assert kent_cmd.take_grants(["-q", "hi"]) == ([], (), ["-q", "hi"])
    assert kent_cmd.sudo_kent(()) == ["sudo", "-n", "-u", "kent", kent_cmd.EXEC]


def test_cli_grants_in_a_child(tmp_path):
    """Renumbering touches descriptors 3..: run it in a child process, never in pytest itself."""
    import subprocess
    a, b = tmp_path / "a", tmp_path / "b"; a.mkdir(); b.mkdir()
    code = ("import importlib.machinery, importlib.util, os, json\n"
            f"l = importlib.machinery.SourceFileLoader('k', {str(ROOT / 'install/services/kent-core/bin/kent')!r})\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('k', l)); l.exec_module(m)\n"
            f"out, fds, rest = m.take_grants(['--grant', {str(a)!r}, '--grant-rw', {str(b) + '/../b'!r}, '-q', 'x'])\n"
            "print(json.dumps([out, fds, rest, [os.readlink(f'/proc/self/fd/{f}') for f in fds],"
            " [os.get_inheritable(f) for f in fds], m.sudo_kent(fds)]))")
    out, fds, rest, paths, inh, sudo = __import__("json").loads(
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)
    assert out == ["--grant", "3", "--grant-rw", "4"] and fds == [3, 4] and rest == ["-q", "x"]
    assert paths == [str(a), str(b)] and inh == [True, True]
    assert sudo[:4] == ["sudo", "-n", "-C", "5"]


def test_cli_missing_folder(kent_cmd, tmp_path):
    with pytest.raises(SystemExit):
        kent_cmd.take_grants(["--grant", str(tmp_path / "nope")])
