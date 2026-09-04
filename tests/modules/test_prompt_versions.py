"""판이 붙는 프롬프트는 문장과 판을 함께 잠근다.

**프롬프트를 파일로 뺀 것이 만드는 유일한 새 위험이다.** 문장이 파이썬 밖으로 나가면
코드를 안 건드리고 고칠 수 있는데 `PROMPT_VERSION`은 코드에 있다. 판을 안 올리고 문장만
바뀌면 ops 창의 채점·판정이 서로 다른 프롬프트의 결과를 한 판으로 섞는다. 같은 파일이던
때는 눈에 띄었지만 파일이 갈리면 안 보인다.

여기서 파일 내용의 SHA-256과 그 흐름의 현재 판을 함께 잠근다.

- 문장만 고치면 해시가 어긋나 깨진다. **판을 올리고 표를 갱신하는 것이 통과 조건이다.**
- 판만 올리면 표에서 키를 못 찾아 깨진다. 둘을 같은 커밋에서 만지게 된다.
- **주석도 해시에 들어간다.** 주석만 고쳐도 깨지는데 그건 받아들인다 — 프롬프트 파일의
  주석은 모델에게 안 가지만 문장을 고치는 사람이 읽는 것이라 같은 무게로 다룬다.

규칙은 `.claude/CLAUDE.md`의 "프롬프트는 코드가 아니다"에 있다.
"""

import hashlib

import pytest

from modules.assessment import PROMPT_VERSION as ASSESSMENT_PROMPT_VERSION
from modules.expectation.domain import PROMPT_VERSION as EXPECTATION_PROMPT_VERSION
from modules.kospi.domain import PROMPT_VERSION as KOSPI_PROMPT_VERSION
from modules.kospi.domain import REVIEW_PROMPT_VERSION as KOSPI_REVIEW_PROMPT_VERSION
from modules.prompt import PROMPT_ROOT

# 판이 붙는 프롬프트 파일. 흐름의 현재 판을 키에 함께 적는다.
# **문장을 고쳤으면 판을 올리고 이 해시도 같이 바꾼다. 둘을 같은 커밋에서 만진다.**
PROMPT_HASHES: dict[tuple[str, str], str] = {
    ("assessment", "3"): "98ca6e74ed7f241abeb7b4b459a86a3c22a459ff8189af6c063d92bc92ea8a79",
    ("expectation_extraction", "1"): "7108eab56e598ff642aeb7269f0f07ab9ef21707798202447db6a6b0a3b52a41",
    # 코스피 일일 전망의 첫 판(2026-09-02). 옛 추론에서 **가져오지 않은 것**이 이 프롬프트를
    # 정의한다 — 3-클래스 확률과 `flat` 기준선 문장은 캘리브레이션 실패의 자리였다.
    ("kospi_forecast", "1"): "73dd161eb811c1a5bc80e8b0b9e61b27b0150abf3902dae01f849466ae16ae47",
    # 판 2는 크기 절을 실측 기준선 위에 다시 썼다(2026-09-03). 판 1은 "최근 진폭에서
    # 출발하라"고만 말해 모델이 봉 열다섯을 눈대중했고, 중앙값 이동 2.27퍼센트인 시장에
    # 폭 1.00퍼센트포인트를 불렀다 — 폭 채점이 구조적으로 뜻을 잃는 값이었다.
    ("kospi_forecast", "2"): "6f31dfae06fbb29ac9886d84e88735aeed8bc295c1bdc0e98c03bc8c5b627094",
    # 판 3은 "폭이 중심값보다 크면 뜻이 없다"를 지웠다(2026-09-03). 방향을 완벽히 맞혀도
    # 필요한 폭이 2.5~2.9%p인데 기대 크기가 약 2.0이라 그 문장이 틀렸다. 닷새 백테스트에서
    # 폭이 1.4~1.8로 눌려 방향이 틀린 날마다 폭도 틀렸다.
    ("kospi_forecast", "3"): "5cfab29d134eacb3c8ce4244f738e557783bede7c47e324e401b0a638c1bfa84",
    ("kospi_forecast", "4"): "62ec5f4981ba52f642ffc76afc35553e08e1f491393040c825971e0db97092fe",
    # 판 5는 장중 지시문에 "가격이 움직였다고 재료가 끝난 것은 아니다"를 넣었다(2026-09-04).
    # 판 4의 장중 문구는 한쪽으로만 밀어 모델이 장전 상방 재료를 갭 소진으로 접고 말없이
    # 뺐다. 앞 슬롯 이유에 요인 코드를 함께 실어 재조회할 손잡이를 줬다.
    ("kospi_forecast", "5"): "7a9f50fd46b08a4b7e754c46ce38c455c5688dcab2acdac8e18f7a2003f06149",
    # 판 6은 `recent_news`에 가치 점수 하한을 뒀다(2026-09-04). **해시가 판 5와 같다** —
    # 문장이 아니라 모델이 보는 기사 묶음이 바뀌었다. 같은 문장을 다른 증거로 돌린 실행은
    # 한 판으로 셀 수 없어서 판만 올린다. 이 표가 잠그는 것은 문장이고, 판을 올릴 이유는
    # 문장 말고도 있다.
    ("kospi_forecast", "6"): "7a9f50fd46b08a4b7e754c46ce38c455c5688dcab2acdac8e18f7a2003f06149",
    # 장후 관찰의 첫 판(2026-09-02). 관찰·새 메모·메모 판정 셋을 한 답에 낸다.
    ("kospi_review", "1"): "e7f0097f2e306984b759ec383d06c08630de98e8043ecb60c10f20f7d5e793f2",
}

# 현재 판을 어디서 읽는지. 표의 키와 대조하는 데만 쓴다.
PROMPT_VERSIONS: dict[str, str] = {
    "assessment": ASSESSMENT_PROMPT_VERSION,
    "expectation_extraction": EXPECTATION_PROMPT_VERSION,
    "kospi_forecast": KOSPI_PROMPT_VERSION,
    "kospi_review": KOSPI_REVIEW_PROMPT_VERSION,
}

# 채점하지 않는 흐름이라 판을 가를 이유가 없는 파일. 표에 넣지 않는다.
UNVERSIONED = {"disclosure_picks", "document_picks"}


@pytest.mark.parametrize(("name", "version"), sorted(PROMPT_VERSIONS.items()))
def test_a_versioned_prompt_file_matches_the_hash_locked_to_its_version(name, version):
    expected = PROMPT_HASHES.get((name, version))
    assert expected is not None, (
        f"{name} 프롬프트의 판이 {version}인데 PROMPT_HASHES에 그 키가 없다. "
        "판을 올렸으면 해시도 같은 커밋에서 갱신한다."
    )

    actual = hashlib.sha256((PROMPT_ROOT / f"{name}.yaml").read_bytes()).hexdigest()

    assert actual == expected, (
        f"{name}.yaml이 판 {version}에 잠긴 내용과 다르다. "
        "문장을 고쳤으면 판을 올리고 PROMPT_HASHES를 함께 갱신한다."
    )


def test_every_prompt_file_is_either_versioned_or_deliberately_not():
    """새 프롬프트 파일이 어느 쪽인지 밝히지 않고 들어오는 것을 막는다."""
    files = {path.stem for path in PROMPT_ROOT.glob("*.yaml")}

    assert files - UNVERSIONED - set(PROMPT_VERSIONS) == set(), (
        "새 프롬프트 파일이 있다. 판이 붙으면 PROMPT_VERSIONS와 PROMPT_HASHES에, "
        "안 붙으면 UNVERSIONED에 넣는다."
    )
    assert set(PROMPT_VERSIONS) - files == set()
    assert UNVERSIONED - files == set()
