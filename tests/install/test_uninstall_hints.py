"""uninstall.sh stops at the first failed module with a plain-language likely cause and the command
to resume; --keep-going keeps the old continue-on-failure behaviour. The hint mapping runs without
root; the loop runs under a user namespace (fake root) against stub modules and a temporary
manifest directory, with --dry-run so the post-loop clean-up never touches the real system."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HINTS = REPO / "install" / "services" / "lib-uninstall-hints.sh"
ORDER = ["gent", "kent-core", "hermes", "searxng", "gitea", "grafana", "alloy", "loki",
         "node_exporter", "prometheus", "litellm", "llama"]


def hint(tmp_path, text: str) -> str:
    f = tmp_path / "out.txt"
    f.write_text(text)
    r = subprocess.run(["bash", "-c", f'source "{HINTS}"; uninstall_hint "{f}"'],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def test_account_in_use_names_user_and_pid(tmp_path):
    h = hint(tmp_path, "[01:11:24] hermes: ...\nuserdel: user kent is currently used by process 4242\n")
    assert "'kent'" in h and "4242" in h and "ps -u kent" in h


@pytest.mark.parametrize("text", [
    "E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 99 (unattended-upgr)",
    "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend)",
])
def test_apt_lock(tmp_path, text):
    assert "apt/dpkg is busy" in hint(tmp_path, text)


def test_docker_down(tmp_path):
    t = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
    assert "Docker is not running" in hint(tmp_path, t)


def test_permission_denied(tmp_path):
    assert "sudo" in hint(tmp_path, "rm: cannot remove '/etc/kent/x': Permission denied")


def test_unknown_falls_back_to_log(tmp_path):
    assert "full log" in hint(tmp_path, "something odd happened")


# --- the loop, under fake root ---------------------------------------------------------------
def fake_root_available() -> bool:
    return shutil.which("unshare") is not None and subprocess.run(
        ["unshare", "-r", "true"], capture_output=True).returncode == 0


needs_userns = pytest.mark.skipif(not fake_root_available(), reason="user namespaces unavailable")


def setup(tmp_path, fail: str | None, message: str = "userdel: user kent is currently used by process 7"):
    """Stub modules that log their name; `fail` exits 1 with `message`. All manifests present."""
    mdir, svc, ran = tmp_path / "manifest", tmp_path / "services", tmp_path / "ran"
    mdir.mkdir()
    for m in ORDER:
        (mdir / m).write_text("user x\n")
        d = svc / m
        d.mkdir(parents=True)
        body = f'echo "{m}" >> "{ran}"\n'
        body += f'echo "{message}" >&2; exit 1\n' if m == fail else f'echo "[00:00:00] {m}: uninstalled"\n'
        (d / "uninstall.sh").write_text("#!/usr/bin/env bash\n" + body)
        (d / "uninstall.sh").chmod(0o755)
    env = {**os.environ, "KENT_MANIFEST_DIR": str(mdir), "KENT_SERVICES_DIR": str(svc)}
    env.pop("SUDO_USER", None)
    return env, ran


def run(env, *args):
    return subprocess.run(["unshare", "-r", str(REPO / "uninstall.sh"), "--dry-run", *args],
                          env=env, capture_output=True, text=True)


@needs_userns
def test_stops_at_first_failure_with_cause_and_resume(tmp_path):
    env, ran = setup(tmp_path, fail="hermes")
    log = tmp_path / "un.log"
    r = run(env, "--log", str(log))
    assert r.returncode == 1
    assert ran.read_text().split() == ["gent", "kent-core", "hermes"]          # nothing after hermes
    assert "✗ (exit 1)" in r.stdout and "likely cause: a process still runs as 'kent'" in r.stdout
    assert "Stopped at 'hermes' (exit 1). Not yet uninstalled: hermes searxng" in r.stdout
    assert f"Full log: {log}" in r.stdout
    resume = next(l for l in r.stdout.splitlines() if l.startswith("  sudo "))
    assert "--dry-run" in resume and str(tmp_path / "un-resume.log") in resume   # failed run's log kept
    assert "likely cause" in log.read_text() and "# stopped at hermes" in log.read_text()


@needs_userns
def test_keep_going_runs_every_module(tmp_path):
    env, ran = setup(tmp_path, fail="hermes")
    r = run(env, "--keep-going")
    assert r.returncode == 1
    assert ran.read_text().split() == ORDER
    assert "failed (--keep-going): hermes" in r.stdout and "Stopped at" not in r.stdout


@needs_userns
def test_success_unchanged(tmp_path):
    env, ran = setup(tmp_path, fail=None)
    r = run(env)
    assert r.returncode == 0 and ran.read_text().split() == ORDER
    assert r.stdout.count("✓") == len(ORDER) and "likely cause" not in r.stdout
