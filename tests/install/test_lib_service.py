"""Layer 5: the install library refuses to adopt or overwrite what Kent didn't
create, records exactly what it creates, and uninstalls only that. Runs
lib-service.sh functions in --dry-run with a temporary manifest (no root)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "install" / "services" / "lib-service.sh"


def sh(tmp_path, script: str, manifest: str = "") -> subprocess.CompletedProcess:
    mdir = tmp_path / "manifest"
    mdir.mkdir(exist_ok=True)
    (mdir / "t").write_text(manifest)
    env = {**os.environ, "KENT_MANIFEST_DIR": str(mdir), "DRY_RUN": "1", "SERVICE": "t"}
    return subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=t; {script}"], env=env,
                          capture_output=True, text=True)


def test_refuses_to_adopt_existing_account(tmp_path):
    r = sh(tmp_path, "ensure_service_account daemon /nonexistent test")
    assert r.returncode != 0 and "refusing to adopt" in r.stderr


def test_reuses_account_it_created(tmp_path):
    r = sh(tmp_path, "ensure_service_account daemon /nonexistent test", "user daemon\ngroup daemon\n")
    assert r.returncode == 0 and "created by Kent" in r.stdout


def test_new_account_is_created_and_recorded(tmp_path):
    r = sh(tmp_path, "ensure_service_account kent-nonexistent-xyz /x t")
    assert r.returncode == 0
    assert "useradd --system --user-group" in r.stdout and "manifest += user kent-nonexistent-xyz" in r.stdout


def test_refuses_existing_group_without_user(tmp_path):
    r = sh(tmp_path, "ensure_service_account adm /x t")   # 'adm' exists as user and group on Ubuntu
    assert r.returncode != 0 and "refusing to adopt" in r.stderr


def test_claim_path_refuses_foreign_path(tmp_path):
    f = tmp_path / "vendor.conf"
    f.write_text("x")
    r = sh(tmp_path, f"claim_path file {f}")
    assert r.returncode != 0 and "refusing to overwrite" in r.stderr


def test_claim_path_allows_own_path(tmp_path):
    f = tmp_path / "ours.conf"
    f.write_text("x")
    assert sh(tmp_path, f"claim_path file {f}", f"file {f}\n").returncode == 0


def test_ensure_dir_never_repermissions_foreign_dir(tmp_path):
    d = tmp_path / "vendor"
    d.mkdir()
    r = sh(tmp_path, f"ensure_dir {d} 0700 root root")
    assert r.returncode == 0 and "install -d" not in r.stdout


def test_ensure_dir_records_missing_ancestors(tmp_path):
    d = tmp_path / "a" / "b" / "c"
    r = sh(tmp_path, f"ensure_dir {d} 0750 root root")
    assert r.returncode == 0
    for p in (tmp_path / "a", tmp_path / "a" / "b", d):
        assert f"manifest += dir {p}" in r.stdout


def test_manifest_add_is_idempotent(tmp_path):
    r = sh(tmp_path, "manifest_add file /x; manifest_add file /x", "file /x\n")
    assert "manifest +=" not in r.stdout


def test_record_unit_state_only_once(tmp_path):
    r = sh(tmp_path, "record_unit_state ssh.service", "unitstate ssh.service enabled active\n")
    assert "manifest +=" not in r.stdout


def test_uninstall_touches_only_manifest_entries(tmp_path):
    m = ("user kent-x\ngroup kent-x\nfile /etc/kent/x.conf\nunit /etc/systemd/system/kent-x.service\n"
         "dir /etc/kent/x\nstate /var/lib/kent-x\n")
    r = sh(tmp_path, "uninstall_module", m)
    out = r.stdout
    assert r.returncode == 0, r.stderr
    assert "rm -f /etc/kent/x.conf" in out and "rm -f /etc/systemd/system/kent-x.service" in out
    assert "kept state: /var/lib/kent-x" in out and "/var/lib/kent-x" not in out.split("kept state")[0].split("rm -rf")[-1]
    for line in out.splitlines():
        if "[dry-run]" in line and any(k in line for k in (" rm ", " userdel ", " groupdel ", " rmdir ")):
            assert any(p in line for p in ("/etc/kent/x", "kent-x", str(tmp_path))), line


def test_uninstall_purge_removes_state(tmp_path):
    r = sh(tmp_path, "PURGE_STATE=1 uninstall_module", "state /var/lib/kent-x\n")
    assert "rm -rf --one-file-system /var/lib/kent-x" in r.stdout


def test_uninstall_restores_moved_paths_and_unit_state(tmp_path):
    bak = tmp_path / "bak"
    bak.mkdir()
    r = sh(tmp_path, "uninstall_module",
           f"unitstate grafana-server.service disabled inactive\nmoved /var/lib/grafana {bak}\n")
    assert "systemctl disable grafana-server.service" in r.stdout
    assert f"mv {bak} /var/lib/grafana" in r.stdout


def test_uninstall_without_manifest_refuses(tmp_path):
    env = {**os.environ, "KENT_MANIFEST_DIR": str(tmp_path / "none"), "DRY_RUN": "1"}
    r = subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=t; uninstall_module"], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "nothing recorded" in r.stderr


def test_uninstall_disables_kent_enabled_vendor_unit_before_purge(tmp_path):
    r = sh(tmp_path, "uninstall_module", "package alloy\nenabled alloy.service\n")
    out = r.stdout
    assert "systemctl disable --now alloy.service" in out
    assert out.index("systemctl disable --now alloy.service") < out.index("apt-get purge")


def test_uninstall_removes_group_membership_it_added(tmp_path):
    user = os.environ.get("USER", "root")
    grp = subprocess.run(["id", "-gn"], capture_output=True, text=True).stdout.strip()
    r = sh(tmp_path, "uninstall_module", f"member {grp} {user}\n")
    assert f"gpasswd -d {user} {grp}" in r.stdout


def test_invocation_args_survive_the_modules_option_loop(tmp_path):
    """Modules source the library before parsing, then shift their arguments away; the
    'install started (...)' audit line must still carry what the operator passed."""
    mod = tmp_path / "install.sh"
    mod.write_text(f'set -euo pipefail\nsource {LIB}\n'
                   'while [[ $# -gt 0 ]]; do shift; done\n'
                   'echo "install started (${INVOCATION_ARGS})"\n')
    mdir = tmp_path / "manifest"
    mdir.mkdir()
    env = {**os.environ, "KENT_MANIFEST_DIR": str(mdir), "DRY_RUN": "1"}
    r = subprocess.run(["bash", str(mod), "--config", "dev", "--no-start"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "install started (--config dev --no-start)"


def test_every_module_logs_its_arguments():
    for f in sorted(LIB.parent.glob("*/install.sh")):
        lines = [l for l in f.read_text().splitlines() if 'audit_event "install started' in l]
        assert lines and all("${INVOCATION_ARGS}" in l for l in lines), f


def test_uninstall_removes_timer_last_run_stamp(tmp_path):
    # Persistent=true timers leave /var/lib/systemd/timers/stamp-<unit>; it goes with the unit.
    r = sh(tmp_path, "uninstall_module",
           "unit /etc/systemd/system/kent-x.timer\nunit /etc/systemd/system/kent-x.service\n")
    assert r.returncode == 0, r.stderr
    assert "rm -f /var/lib/systemd/timers/stamp-kent-x.timer" in r.stdout
    assert "stamp-kent-x.service" not in r.stdout


def test_uninstall_removes_user_timer_stamp_in_operator_home(tmp_path):
    user = os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    home = subprocess.run(["getent", "passwd", user], capture_output=True, text=True).stdout.split(":")[5]
    r = sh(tmp_path, "uninstall_module",
           f"userunit {user} {home}/.config/systemd/user/kent-y.timer\n"
           f"userunit {user} {home}/.config/systemd/user/kent-y.service\n")
    assert r.returncode == 0, r.stderr
    assert f"rm -f {home}/.local/share/systemd/timers/stamp-kent-y.timer" in r.stdout
    assert "stamp-kent-y.service" not in r.stdout


def _fake_systemctl(tmp_path, state: str) -> dict:
    """PATH shim: `systemctl ... is-enabled U` prints <state>; any other call just succeeds."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "systemctl").write_text(
        "#!/bin/sh\n"
        f'case " $* " in *" is-enabled "*) echo {state}; [ {state} = enabled ] ;; *) exit 0 ;; esac\n')
    (bindir / "systemctl").chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}


def sh_env(tmp_path, script: str, env: dict, manifest: str = "") -> subprocess.CompletedProcess:
    mdir = tmp_path / "manifest"
    mdir.mkdir(exist_ok=True)
    (mdir / "t").write_text(manifest)
    env = {**env, "KENT_MANIFEST_DIR": str(mdir), "DRY_RUN": "1", "SERVICE": "t"}
    return subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=t; {script}"], env=env,
                          capture_output=True, text=True)


def test_unit_off_disables_an_enabled_unit(tmp_path):
    r = sh_env(tmp_path, "unit_off kent-x.service", _fake_systemctl(tmp_path, "enabled"))
    assert r.returncode == 0 and "systemctl disable --now kent-x.service" in r.stdout


@pytest.mark.parametrize("state", ["static", "indirect", "disabled", "generated", "not-found"])
def test_unit_off_only_stops_units_that_are_not_enabled(tmp_path, state):
    r = sh_env(tmp_path, "unit_off kent-x.service", _fake_systemctl(tmp_path, state))
    assert r.returncode == 0 and "systemctl stop kent-x.service" in r.stdout and "disable" not in r.stdout


def test_unit_off_passes_the_user_scope(tmp_path):
    r = sh_env(tmp_path, "unit_off kent-x.timer --user -M op@", _fake_systemctl(tmp_path, "static"))
    assert "systemctl --user -M op@ stop kent-x.timer" in r.stdout


def test_uninstall_of_a_missing_unit_does_not_abort(tmp_path):
    unit = tmp_path / "kent-nonexistent-xyz.service"
    r = sh_env(tmp_path, "uninstall_module", dict(os.environ), f"unit {unit}\n")
    assert r.returncode == 0 and f"rm -f {unit}" in r.stdout


# --- Download cache (fetch_verified) and pinned .deb packages --------------------------------
def fetch(tmp_path, src: Path, sha: str, *, cache: Path, extra_env: dict | None = None, dry: bool = False):
    """Run fetch_verified for a file:// URL outside dry-run (curl reads the local file)."""
    mdir = tmp_path / "manifest"; mdir.mkdir(exist_ok=True); (mdir / "t").write_text("")
    env = {k: v for k, v in os.environ.items() if k != "KENT_NO_CACHE"}
    env |= {"KENT_MANIFEST_DIR": str(mdir), "DRY_RUN": "1" if dry else "0", "SERVICE": "t",
            "KENT_CACHE": str(cache), **(extra_env or {})}
    dest = tmp_path / "out.bin"
    r = subprocess.run(["bash", "-c", f"source {LIB}; SERVICE=t; fetch_verified file://{src} {sha} {dest}"],
                       env=env, capture_output=True, text=True)
    return r, dest


@pytest.fixture
def artifact(tmp_path):
    import hashlib
    src = tmp_path / "pkg-1.0.tgz"; src.write_bytes(b"pinned artifact\n")
    return src, hashlib.sha256(src.read_bytes()).hexdigest()


def test_fetch_stores_a_verified_download_in_the_cache(tmp_path, artifact):
    src, sha = artifact
    r, dest = fetch(tmp_path, src, sha, cache=tmp_path / "cache")
    assert r.returncode == 0, r.stderr
    assert dest.read_bytes() == src.read_bytes()
    assert (tmp_path / "cache/downloads" / f"{sha}-{src.name}").read_bytes() == src.read_bytes()


def test_fetch_uses_the_cache_without_the_network(tmp_path, artifact):
    src, sha = artifact
    fetch(tmp_path, src, sha, cache=tmp_path / "cache")
    data = src.read_bytes(); src.unlink()                  # "offline": the URL no longer resolves
    r, dest = fetch(tmp_path, src, sha, cache=tmp_path / "cache")
    assert r.returncode == 0, r.stderr
    assert "from the download cache" in r.stdout and dest.read_bytes() == data


def test_fetch_replaces_a_cached_file_that_fails_its_hash(tmp_path, artifact):
    src, sha = artifact
    cached = tmp_path / "cache/downloads" / f"{sha}-{src.name}"
    cached.parent.mkdir(parents=True); cached.write_bytes(b"tampered\n")
    r, dest = fetch(tmp_path, src, sha, cache=tmp_path / "cache")
    assert r.returncode == 0, r.stderr
    assert "does not match its pinned SHA-256" in r.stderr
    assert dest.read_bytes() == src.read_bytes() and cached.read_bytes() == src.read_bytes()


def test_fetch_refuses_a_download_that_fails_its_hash_and_caches_nothing(tmp_path, artifact):
    src, _ = artifact
    r, dest = fetch(tmp_path, src, "0" * 64, cache=tmp_path / "cache")
    assert r.returncode != 0 and "SHA-256 mismatch" in r.stderr
    assert not dest.exists() and not list((tmp_path / "cache").rglob("*.tgz"))


def test_no_cache_neither_reads_nor_writes_the_cache(tmp_path, artifact):
    src, sha = artifact
    cached = tmp_path / "cache/downloads" / f"{sha}-{src.name}"
    cached.parent.mkdir(parents=True); cached.write_bytes(src.read_bytes())
    src.unlink()                                           # a cache read would succeed; a download fails
    r, _ = fetch(tmp_path, src, sha, cache=tmp_path / "cache", extra_env={"KENT_NO_CACHE": "1"})
    assert r.returncode != 0 and "download failed" in r.stderr
    src.write_bytes(cached.read_bytes()); cached.unlink()
    r, _ = fetch(tmp_path, src, sha, cache=tmp_path / "cache", extra_env={"KENT_NO_CACHE": "1"})
    assert r.returncode == 0 and not cached.exists()


def test_dry_run_says_whether_the_cache_would_be_used(tmp_path, artifact):
    src, sha = artifact
    r, _ = fetch(tmp_path, src, sha, cache=tmp_path / "cache", dry=True)
    assert "[dry-run] download" in r.stdout and not (tmp_path / "cache").exists()
    fetch(tmp_path, src, sha, cache=tmp_path / "cache")
    r, _ = fetch(tmp_path, src, sha, cache=tmp_path / "cache", dry=True)
    assert "[dry-run] use cached" in r.stdout


def test_pinned_deb_is_installed_from_the_verified_file_and_recorded(tmp_path):
    r = sh(tmp_path, "ensure_pinned_deb kent-nonexistent-pkg 1.2.3 https://example.invalid/p_1.2.3.deb "
                     + "a" * 64)
    assert r.returncode == 0, r.stderr
    assert "download https://example.invalid/p_1.2.3.deb" in r.stdout
    assert "apt-get install -y --no-install-recommends --allow-downgrades" in r.stdout and "p_1.2.3.deb" in r.stdout
    assert "manifest += package kent-nonexistent-pkg" in r.stdout


def test_retire_grafana_repo_only_touches_a_repo_kent_added(tmp_path):
    assert "Grafana apt repository" not in sh(tmp_path, "retire_grafana_repo").stdout
    r = sh(tmp_path, "retire_grafana_repo", "file /etc/apt/sources.list.d/grafana.list\nfile /etc/apt/keyrings/grafana.asc\n")
    assert "rm -f /etc/apt/sources.list.d/grafana.list" in r.stdout and "rm -f /etc/apt/keyrings/grafana.asc" in r.stdout


def test_modules_install_grafana_and_alloy_from_pins_not_an_apt_source():
    svc = LIB.parent
    for mod, var in (("grafana", "GRAFANA"), ("alloy", "ALLOY")):
        text = (svc / mod / "install.sh").read_text()
        assert f'ensure_pinned_deb {mod} "${var}_VERSION"' in text
        assert "ensure_package " + mod not in text and "sources.list.d" not in text
    env = (svc / "versions.env").read_text()
    for key in ("GRAFANA_VERSION", "GRAFANA_DEB_URL", "GRAFANA_DEB_SHA256", "ALLOY_VERSION", "ALLOY_DEB_URL",
                "ALLOY_DEB_SHA256"):
        assert f"\n{key}=" in env
