"""장중 대시보드 여섯 벌이 서로 어긋나지 않는지 확인한다.

대시보드를 `kind`별로 나눠 두는 이유는 **잘못 읽을 수 없게 하기 위해서다.** 종류마다
정상 변동폭이 다르고(측정: 15분 변화율 상위 1%가 지수 3.081% 대 암호화폐 0.166%로 18배),
임계값 기본값이 화면마다 박혀 있어야 급변 이벤트 표가 조용히 비거나 조용히 넘치지 않는다.

**대신 같은 패널이 여섯 벌로 복사된다.** 하나만 고치면 나머지 다섯이 다른 숫자를 보여
주는데, 화면은 멀쩡해서 눈으로는 알 수 없다. 나누기를 택한 대가가 이 위험이고 여기서 막는다.
"""

import json
import re
from pathlib import Path

import pytest

from apps.models.reference import QuoteSymbolKind

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards"
PATHS = sorted(DASHBOARD_DIR.glob("quote-*.json"))

# 통합 대시보드는 종류를 가리지 않는다. 나머지는 `kind` 하나로 좁힌다.
UNIFIED = "quote-intraday.json"

# 패널 SQL 이 대시보드마다 달라도 되는 자리는 여기 적힌 것뿐이다. 이걸 걷어낸 뒤에도
# 다르면 복사본이 어긋난 것이다.
#
# - `kind` 조건: 종류별 화면이 자기 종류로 좁히는 자리.
# - 급변 이벤트의 "종류" 열: 통합 화면에만 있다. 한 종류만 보는 화면에서는 모든 행이
#   같은 값이라 열 하나를 낭비한다.
ALLOWED_DIFFERENCES = (
    re.compile(r"\s*AND kind = '[a-z_]+'"),
    re.compile(r"\n *m\.kind AS \"종류\","),
    re.compile(r"\nJOIN quote_symbol m ON m\.symbol = islands\.symbol"),
)
UNIFIED_GROUP_BY = ("GROUP BY islands.display, m.kind, grp", "GROUP BY islands.display, grp")


def normalized(sql: str) -> str:
    for pattern in ALLOWED_DIFFERENCES:
        sql = pattern.sub("", sql)
    return sql.replace(*UNIFIED_GROUP_BY)


SESSION_VALUES = ("all", "kr", "us", "off", "weekend")


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def variables(dashboard: dict) -> dict[str, dict]:
    return {variable["name"]: variable for variable in dashboard["templating"]["list"]}


def panel_sql(dashboard: dict) -> dict[int, str]:
    return {
        panel["id"]: target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "rawSql" in target
    }


def test_there_are_dashboards_to_check():
    assert len(PATHS) >= 2


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.name)
def test_every_dashboard_carries_the_same_variables(path):
    # 변수 하나가 빠지면 그 화면만 다른 조건으로 조회한다.
    assert list(variables(load(path))) == ["provider", "symbol", "session", "window_min", "threshold"]


@pytest.mark.parametrize("path", [p for p in PATHS if p.name != UNIFIED], ids=lambda p: p.name)
def test_panel_sql_matches_the_unified_dashboard(path):
    """`kind` 조건을 걷어내면 모든 대시보드의 패널 SQL 이 글자 그대로 같아야 한다.

    이게 나누기의 유일한 실질적 위험이다. 패널 하나를 고치고 나머지를 안 고치면
    같은 심볼이 화면마다 다른 숫자로 보이는데 오류가 나지 않는다.
    """
    unified = panel_sql(load(DASHBOARD_DIR / UNIFIED))
    mine = panel_sql(load(path))

    assert set(mine) == set(unified)
    for panel_id, sql in mine.items():
        assert normalized(sql) == normalized(unified[panel_id]), f"패널 {panel_id}"


@pytest.mark.parametrize("path", [p for p in PATHS if p.name != UNIFIED], ids=lambda p: p.name)
def test_narrow_dashboards_pin_exactly_one_kind(path):
    dashboard = load(path)
    kinds = set(re.findall(r"kind = '([a-z_]+)'", json.dumps(dashboard, ensure_ascii=False)))

    assert len(kinds) == 1, kinds
    assert kinds <= {kind.value for kind in QuoteSymbolKind}


def test_every_kind_that_needs_a_screen_has_one():
    """`kind`를 늘리고 대시보드를 안 만들면 그 종류는 통합 화면에서만 보인다.

    통합은 임계값이 하나라 종류를 좁혀 볼 수 없다. `rate`·`bond_future`·`equity`는
    심볼이 하나씩뿐이라 전용 화면을 두지 않았고, 늘어나면 그때 만든다.
    """
    covered = set()
    for path in PATHS:
        if path.name == UNIFIED:
            continue
        covered |= set(re.findall(r"kind = '([a-z_]+)'", path.read_text()))

    assert {"index", "index_future", "fx", "commodity", "crypto"} <= covered


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.name)
@pytest.mark.parametrize("session", SESSION_VALUES)
def test_every_session_value_is_handled(session, path):
    """구간 변수 값과 패널 SQL 이 어긋나면 그 구간이 조용히 빈 화면이 된다.

    SQL 이 `'$session' = 'kr'` 같은 문자열 비교라 오타가 나도 오류가 아니라 0행이다.
    """
    dashboard = load(path)
    assert session in {option["value"] for option in variables(dashboard)["session"]["options"]}

    scoped = [sql for sql in panel_sql(dashboard).values() if "'$session'" in sql]
    assert scoped, "구간을 반영하는 패널이 하나도 없다"
    assert all(f"'{session}'" in sql for sql in scoped)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.name)
def test_session_boundaries_use_local_time_not_a_fixed_offset(path):
    """미 정규장은 서머타임에 따라 KST 22:30 이었다가 23:30 이 된다.

    KST 상수로 쓰면 1년에 두 번 조용히 틀리므로 뉴욕 현지 시각으로 판정한다.
    """
    scoped = [sql for sql in panel_sql(load(path)).values() if "'$session' = 'us'" in sql]
    assert scoped

    for sql in scoped:
        assert "AT TIME ZONE 'America/New_York'" in sql
        assert "'22:30'" not in sql


# 실측한 15분 변화율 상위 1%. 임계값 기본값이 이보다 훨씬 높으면 급변 이벤트 표가 늘 비고,
# 훨씬 낮으면 조용한 분까지 전부 잡혀 "급변"이 의미를 잃는다.
MEASURED_TOP_ONE_PERCENT = {
    "index": 3.081,
    "index_future": 0.464,
    "fx": 0.236,
    "commodity": 0.966,
    "crypto": 0.166,
    # 2026-08-12~14 삼성전자·SK하이닉스 2,195표본. 지수보다 낮고 지수선물보다 훨씬 높다.
    "equity": 1.833,
}


@pytest.mark.parametrize("path", [p for p in PATHS if p.name != UNIFIED], ids=lambda p: p.name)
def test_threshold_default_is_in_range_for_its_kind(path):
    """임계값 기본값이 그 종류의 실제 분포와 같은 자릿수인지 본다.

    처음에 암호화폐 대시보드를 원자재에서 복사하면서 0.5를 물려받았다. 암호화폐의 상위 1%가
    0.166%라 **급변 이벤트가 영원히 0건**이었다. 빈 표는 "급변이 없었다"로 읽힌다.
    """
    dashboard = load(path)
    kind = re.findall(r"kind = '([a-z_]+)'", json.dumps(dashboard, ensure_ascii=False))[0]
    threshold = float(variables(dashboard)["threshold"]["current"]["value"])
    top = MEASURED_TOP_ONE_PERCENT[kind]

    # 상위 1% 의 3배를 넘으면 사실상 아무것도 안 잡히고, 1/5 아래면 너무 많이 잡힌다.
    assert top / 5 <= threshold <= top * 3, f"{kind}: 임계값 {threshold} 대 상위 1% {top}"
