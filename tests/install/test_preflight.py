"""The uninstall pre-flight check (install/services/lib-preflight.sh) finds, before anything is
removed, each condition that would make a module fail halfway, and passes a clean system.
Runs without root: fake manifests (KENT_MANIFEST_DIR) and stub commands on PATH."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "install" / "services" / "lib-preflight.sh"
UNINSTALL = REPO / "uninstall.sh"


def stub(bindir: Path, name: str, body: str) -> None:
    f = bindir / name
    f.write_text("#!/usr/bin/env bash\n" + body + "\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)


def setup(tmp_path, manifests: dict[str, str], *, procs: str = "", docker: bool = True,
          dpkg_busy: bool = False, fuser: bool = True, unit: str = "session-3.scope",
          ps_unit: bool = True, kent_label: str = ""):
    mdir = tmp_path / "manifest"
    mdir.mkdir()
    for name, text in manifests.items():
        (mdir / name).write_text(text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Every account "exists"; pgrep reports PIDs only for the accounts named in `procs`.
    stub(bindir, "getent", 'echo "$2:x:1:1::/:/usr/sbin/nologin"')
    stub(bindir, "pgrep", f'[[ "$1" == -u && " {procs} " == *" $2 "* ]] && echo 4242 && exit 0; '
                          f'[[ "$1" == -x ]] && {"exit 0" if dpkg_busy else "exit 1"}; exit 1')
    stub(bindir, "systemctl", f'[[ "$*" == *"is-active"*docker* ]] && exit {0 if docker else 3}; exit 0')
    if fuser:
        stub(bindir, "fuser", f'exit {0 if dpkg_busy else 1}')
    # ps -o unit= reports the systemd unit of PID 4242; without ps_unit, ps lacks that field.
    stub(bindir, "kps", f'echo {unit}' if ps_unit else 'echo "error: unknown user-defined format specifier" >&2; exit 1')
    stub(bindir, "kdocker", f'echo "{kent_label or "<no value>"}"')
    lock = tmp_path / "lock-frontend"
    lock.write_text("")
    env = {**os.environ, "KENT_MANIFEST_DIR": str(mdir), "KENT_DPKG_LOCKS": str(lock),
           "PATH": f"{bindir}:{os.environ['PATH']}"}
    env["KENT_PS"], env["KENT_DOCKER"] = str(bindir / "kps"), str(bindir / "kdocker")
    if not fuser:   # a system without fuser
        env["KENT_FUSER"] = "kent-no-such-fuser"
    return env


def check(env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f"source {LIB}; preflight_check"], env=env,
                          capture_output=True, text=True)


def test_clean_system_passes(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    env = setup(tmp_path, {"litellm": "user litellm\npackage alloy\ndockernet kent-gent-net\n",
                           "llama": f"modelsdir {models}\n"})
    r = check(env)
    assert r.returncode == 0 and "✗" not in r.stdout


def test_nothing_installed_is_not_a_problem(tmp_path):
    env = setup(tmp_path, {})
    r = check(env)
    assert r.returncode == 0 and "nothing recorded" in r.stdout


def test_unknown_manifest_kind_is_reported(tmp_path):
    env = setup(tmp_path, {"gent": "user kent-squid\nbogus /etc/x\n"})
    r = check(env)
    assert r.returncode == 1 and "does not understand" in r.stdout and "bogus /etc/x" in r.stdout


def test_running_process_of_kent_account_is_reported(tmp_path):
    env = setup(tmp_path, {"hermes": "user kent\n", "litellm": "user litellm\n"}, procs="kent")
    r = check(env)
    assert r.returncode == 1 and "'kent' has processes outside Kent's services" in r.stdout
    assert "session-3.scope PID 4242" in r.stdout and "'litellm'" not in r.stdout


def test_process_in_recorded_kent_unit_passes(tmp_path):
    env = setup(tmp_path, {"litellm": "user litellm\nunit /etc/systemd/system/kent-litellm.service\n"},
                procs="litellm", unit="kent-litellm.service")
    assert check(env).returncode == 0


def test_process_in_kent_named_unit_passes(tmp_path):
    env = setup(tmp_path, {"hermes": "user kent\n"}, procs="kent", unit="kent-poll-learnings.service")
    assert check(env).returncode == 0


def test_process_in_vendor_unit_recorded_as_enabled_passes(tmp_path):
    env = setup(tmp_path, {"alloy": "user alloy\nenabled alloy.service\n"}, procs="alloy", unit="alloy.service")
    assert check(env).returncode == 0


def test_process_in_kent_container_passes_other_container_fails(tmp_path):
    kent = setup(tmp_path, {"searxng": "user kent-searxng\n"}, procs="kent-searxng",
                 unit="docker-abc123.scope", kent_label="searxng")
    assert check(kent).returncode == 0
    (tmp_path / "o").mkdir()
    other = setup(tmp_path / "o", {"searxng": "user kent-searxng\n"}, procs="kent-searxng",
                  unit="docker-abc123.scope")
    assert "docker-abc123.scope PID 4242" in check(other).stdout


def test_without_unit_information_says_it_cannot_tell(tmp_path):
    env = setup(tmp_path, {"hermes": "user kent\n"}, procs="kent", ps_unit=False)
    r = check(env)
    assert r.returncode == 1 and "cannot tell" in r.stdout


def test_docker_stopped_with_docker_objects_is_reported(tmp_path):
    env = setup(tmp_path, {"gent": "dockerimage kent-gent\n"}, docker=False)
    r = check(env)
    assert r.returncode == 1 and "Docker is not running" in r.stdout


def test_docker_stopped_with_container_unit_is_reported(tmp_path):
    unit = tmp_path / "kent-searxng.service"
    unit.write_text("[Service]\nExecStart=/usr/bin/docker run --rm x\n")
    env = setup(tmp_path, {"searxng": f"unit {unit}\n"}, docker=False)
    assert "Docker is not running" in check(env).stdout


def test_docker_stopped_without_docker_objects_is_fine(tmp_path):
    env = setup(tmp_path, {"loki": "user loki\n"}, docker=False)
    assert check(env).returncode == 0


def test_dpkg_lock_held_with_packages_is_reported(tmp_path):
    env = setup(tmp_path, {"alloy": "package alloy\n"}, dpkg_busy=True)
    r = check(env)
    assert r.returncode == 1 and "dpkg lock" in r.stdout and "alloy" in r.stdout


def test_dpkg_lock_without_fuser_falls_back_to_process_check(tmp_path):
    env = setup(tmp_path, {"alloy": "package alloy\n"}, dpkg_busy=True, fuser=False)
    assert "dpkg lock" in check(env).stdout


def test_dpkg_lock_irrelevant_without_packages(tmp_path):
    env = setup(tmp_path, {"loki": "user loki\n"}, dpkg_busy=True)
    assert check(env).returncode == 0


def test_missing_model_directory_is_reported(tmp_path):
    env = setup(tmp_path, {"llama": f"modelsdir {tmp_path}/not-mounted/models\n"})
    r = check(env)
    assert r.returncode == 1 and "model directory" in r.stdout and "not mounted" in r.stdout


def test_problems_are_counted(tmp_path):
    env = setup(tmp_path, {"llama": f"user kent-llama\npackage x\nmodelsdir {tmp_path}/gone\n"},
                procs="kent-llama", dpkg_busy=True)
    assert check(env).returncode == 3


def test_uninstall_check_option_needs_no_root(tmp_path):
    env = setup(tmp_path, {"hermes": "user kent\n"}, procs="kent")
    r = subprocess.run([str(UNINSTALL), "--check"], env=env, capture_output=True, text=True)
    assert r.returncode == 3 and "outside Kent's services" in r.stdout
    (tmp_path / "ok").mkdir()
    ok = setup(tmp_path / "ok", {"loki": "user loki\n"})
    r = subprocess.run([str(UNINSTALL), "--check"], env=ok, capture_output=True, text=True)
    assert r.returncode == 0 and "ready to uninstall" in r.stdout


def test_every_manifest_kind_the_installers_write_is_known():
    """A kind written by an installer but missing from PREFLIGHT_KINDS would make the pre-flight
    refuse every uninstall ("lines the uninstaller does not understand")."""
    import re
    kinds = set(re.search(r'PREFLIGHT_KINDS="([^"]+)"', LIB.read_text()).group(1).split())
    src = "\n".join(p.read_text() for p in (REPO / "install" / "services").rglob("*.sh"))
    written = set(re.findall(r"manifest_add ([a-z]+) ", src))
    written |= set(re.findall(r"(?:place_file|claim_path) ([a-z]+) ", src))
    assert written and written <= kinds, sorted(written - kinds)
