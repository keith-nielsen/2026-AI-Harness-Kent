"""Every provisioned Grafana dashboard parses, has a unique uid, and only uses data sources
that configs/grafana/datasources.yaml provisions (a wrong uid renders as an empty panel)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

GRAFANA = Path(__file__).resolve().parents[2] / "configs" / "grafana"


def test_dashboards_use_only_provisioned_datasources():
    provisioned = {ds["uid"] for ds in yaml.safe_load((GRAFANA / "datasources.yaml").read_text())["datasources"]}
    uids = []
    for path in sorted(GRAFANA.glob("kent-*.json")):
        dash = json.loads(path.read_text())
        uids.append(dash["uid"])
        for panel in dash["panels"]:
            used = [panel.get("datasource")] + [t.get("datasource") for t in panel.get("targets", [])]
            for ds in filter(None, used):
                assert ds["uid"] in provisioned, f"{path.name}: panel '{panel['title']}' uses unknown {ds['uid']}"
    assert len(uids) >= 2 and len(uids) == len(set(uids))
