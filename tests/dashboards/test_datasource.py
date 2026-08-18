import json
from pathlib import Path

import pytest


DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards"
PATHS = sorted(DASHBOARD_DIR.glob("*.json"))


def datasource_uids(value):
    if isinstance(value, dict):
        datasource = value.get("datasource")
        if isinstance(datasource, dict) and "uid" in datasource:
            yield datasource["uid"]
        for child in value.values():
            yield from datasource_uids(child)
    elif isinstance(value, list):
        for child in value:
            yield from datasource_uids(child)


@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.name)
def test_every_dashboard_uses_the_local_finance_datasource(path):
    dashboard = json.loads(path.read_text())

    assert "ds" not in {variable["name"] for variable in dashboard["templating"]["list"]}
    assert set(datasource_uids(dashboard)) == {"news2-postgres"}
