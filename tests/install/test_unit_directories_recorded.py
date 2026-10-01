"""Every directory systemd creates for a Kent unit (StateDirectory=, CacheDirectory=,
LogsDirectory=, ConfigurationDirectory=) is recorded by that module's install.sh, so uninstall
removes it. RuntimeDirectory= lives under /run and is removed by systemd itself. Static check of
the repository (no root): an unrecorded directory is left behind as a file owned by a deleted
account (found on 2026-09-29: /var/cache/kent-llama)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[2] / "install" / "services"
BASES = {"StateDirectory": "/var/lib", "CacheDirectory": "/var/cache",
         "LogsDirectory": "/var/log", "ConfigurationDirectory": "/etc"}
UNIT_GLOBS = ("*.service", "*.socket", "*.mount.in", "kent.conf")


def unit_files():
    for pattern in UNIT_GLOBS:
        yield from SERVICES.glob(f"*/**/{pattern}")
        yield from SERVICES.glob(f"*/{pattern}")


def directives():
    seen = set()
    for unit in unit_files():
        if unit in seen:
            continue
        seen.add(unit)
        module = unit.relative_to(SERVICES).parts[0]
        text = unit.read_text()
        dynamic = re.search(r"^DynamicUser=(yes|true|1)\s*$", text, re.M) is not None
        for key, base in BASES.items():
            for m in re.finditer(rf"^{key}=(.+)$", text, re.M):
                for name in m.group(1).split():
                    name = name.split(":")[0]            # "dir:mode" forms
                    root = f"{base}/private" if dynamic and key != "ConfigurationDirectory" else base
                    yield pytest.param(module, unit.name, key, f"{root}/{name}", id=f"{unit.name}:{key}={name}")


def recorded(module: str, path: str) -> bool:
    """install.sh records the path (manifest_add state|path), literally or through a variable."""
    src = (SERVICES / module / "install.sh").read_text()
    if re.search(rf'manifest_add (state|path) "?{re.escape(path)}"?\s*$', src, re.M):
        return True
    for var in re.findall(rf'^\s*([A-Z_][A-Z0-9_]*)="?{re.escape(path)}"?\s*$', src, re.M):
        if re.search(rf'manifest_add (state|path) "?\$\{{?{var}\}}?"?\s*$', src, re.M):
            return True
    return False


@pytest.mark.parametrize("module,unit,key,path", list(directives()))
def test_systemd_managed_directory_is_recorded(module, unit, key, path):
    assert recorded(module, path), (
        f"{unit} ({module}) has {key} → {path}, but install/services/{module}/install.sh does not "
        f"record it (manifest_add state|path {path}); uninstall would leave it behind")


def test_scan_finds_the_known_directories():
    # Guard against the scan silently matching nothing.
    found = {p.values[3] for p in directives()}
    assert {"/var/cache/kent-llama", "/var/lib/litellm", "/var/log/kent-squid"} <= found
