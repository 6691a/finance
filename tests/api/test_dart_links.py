"""공시·실적의 원문 링크.

접수번호(`rcept_no`)만 보여 주면 사람이 DART에서 그 번호를 다시 찾아야 한다. **그 번호가
곧 주소다** — 그래서 컬럼을 더하지 않고 응답에서 만든다.

주소 틀이 이 저장소에 둘이다(브리핑의 `modules.thesis.domain`과 조회 API). 두 트리는
서로를 import하지 않으므로 **중복을 허용하고 여기서 대조한다** — 저장소 규칙의
`*_match_the_airflow_collector`와 같은 자리다.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from apps.api.service.common import DART_VIEWER, dart_url
from apps.api.service.document import disclosure_of, earnings_of
from modules.thesis.domain import DART_VIEWER_URL

RCEPT = "20260827000123"


def _disclosure(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        rcept_no=RCEPT,
        stock_code="005930",
        corp_code="00126380",
        company_name="삼성전자",
        report_name="주요사항보고서",
        filer_name="삼성전자",
        corp_class="Y",
        receipt_date=date(2026, 8, 27),
        detected_at=datetime(2026, 8, 27, 4, tzinfo=UTC),
        remarks=None,
        body=None,
        provider=provider,
    )


def _earnings(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        stock_code="005930",
        rcept_no=RCEPT,
        release_type="periodic",
        period_end=date(2026, 6, 30),
        statement_scope="CFS",
        amount_basis="cumulative",
        metric="revenue",
        current_amount=Decimal(74000000000000),
        prior_year_amount=Decimal(60000000000000),
        currency="KRW",
        source_account_name="매출액",
        provider=provider,
    )


def test_the_receipt_number_is_the_address():
    assert dart_url("dart", RCEPT) == f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RCEPT}"


def test_another_provider_gets_no_link():
    """다른 제공처의 번호를 DART 주소에 끼우면 없는 문서로 가는 링크가 된다.

    오늘 두 표는 전부 `dart`라 이 가드가 막는 행이 없다(2026-08-27 운영 DB 실측:
    공시 46건, 실적 6건 전부). 자연키가 `(provider, rcept_no)`라 제공처가 늘 수 있고,
    그때 조용히 깨진 링크가 생기는 것을 막는 자리다.
    """
    assert dart_url("kind", RCEPT) is None


def test_both_rows_carry_the_link():
    assert disclosure_of(_disclosure("dart")).url == dart_url("dart", RCEPT)
    assert earnings_of(_earnings("dart")).url == dart_url("dart", RCEPT)


def test_a_row_without_a_link_still_shows_its_number():
    """링크가 없다고 행이 사라지지 않는다. 화면은 번호를 글자 그대로 찍는다."""
    row = disclosure_of(_disclosure("kind"))
    assert row.url is None
    assert row.rcept_no == RCEPT


def test_the_viewer_template_matches_the_briefing():
    """브리핑과 화면이 같은 주소를 내야 한다. 한쪽만 고치면 두 출력이 갈린다."""
    assert DART_VIEWER == DART_VIEWER_URL
