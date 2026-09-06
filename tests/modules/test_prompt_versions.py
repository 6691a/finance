"""판이 붙는 프롬프트는 문장과 판을 함께 잠근다.

**프롬프트를 파일로 뺀 것이 만드는 유일한 새 위험이다.** 문장이 파이썬 밖으로 나가면
코드를 안 건드리고 고칠 수 있는데 `PROMPT_VERSION`은 코드에 있다. 판을 안 올리고 문장만
바뀌면 ops 창의 채점·판정이 서로 다른 프롬프트의 결과를 한 판으로 섞는다. 같은 파일이던
때는 눈에 띄었지만 파일이 갈리면 안 보인다.

여기서 파일 내용의 SHA-256과 그 흐름의 현재 판을 함께 잠근다. **응답 모델의 JSON Schema도
같은 해시에 넣는다**(2026-09-06) — 출력 모양은 YAML이 아니라 Pydantic 모델의
`Field(description=...)`이 말하고 그것이 `response_format`으로 모델에게 가므로, description을
고치는 것도 문장을 고치는 것이다.

- 문장만 고치면 해시가 어긋나 깨진다. **판을 올리고 표를 갱신하는 것이 통과 조건이다.**
- 판만 올리면 표에서 키를 못 찾아 깨진다. 둘을 같은 커밋에서 만지게 된다.
- **주석도 해시에 들어간다.** 주석만 고쳐도 깨지는데 그건 받아들인다 — 프롬프트 파일의
  주석은 모델에게 안 가지만 문장을 고치는 사람이 읽는 것이라 같은 무게로 다룬다.

규칙은 `.claude/CLAUDE.md`의 "프롬프트는 코드가 아니다"에 있다.
"""

import hashlib
import json

import pytest
from pydantic import BaseModel

from modules.assessment import PROMPT_VERSION as ASSESSMENT_PROMPT_VERSION
from modules.assessment import Assessment
from modules.expectation.domain import PROMPT_VERSION as EXPECTATION_PROMPT_VERSION
from modules.expectation.extraction import ExtractionResponse
from modules.kospi.domain import PROMPT_VERSION as KOSPI_PROMPT_VERSION
from modules.kospi.domain import REVIEW_PROMPT_VERSION as KOSPI_REVIEW_PROMPT_VERSION
from modules.kospi.generation import ForecastAnswer, ReviewAnswer
from modules.prompt import PROMPT_ROOT
from modules.schema import strict_json_schema
from modules.shock.domain import CAUSE_PROMPT_VERSION as SHOCK_CAUSE_PROMPT_VERSION
from modules.shock.domain import CauseAnswer

# 판이 붙는 프롬프트 파일. 흐름의 현재 판을 키에 함께 적는다.
# **문장을 고쳤으면 판을 올리고 이 해시도 같이 바꾼다. 둘을 같은 커밋에서 만진다.**
PROMPT_HASHES: dict[tuple[str, str], str] = {
    ("assessment", "3"): "98ca6e74ed7f241abeb7b4b459a86a3c22a459ff8189af6c063d92bc92ea8a79",
    # 판 4는 외부 글 읽기 규칙 조각(`$untrusted_text`)을 넣고 제목·요약을 `<외부자료>`로
    # 감쌌다(2026-09-06, docs/convention/prompt-injection-defense.md). 판이 오르면 아카이브
    # 전체가 재평가 대상이 된다 — 그것을 알고 올렸다.
    ("assessment", "4"): "f4d4c26795895fb8c026b110f2fea86448d442aa8696899f86a056079a4392e3",
    ("expectation_extraction", "1"): "7108eab56e598ff642aeb7269f0f07ab9ef21707798202447db6a6b0a3b52a41",
    # 판 2는 같은 조각과 구분자를 넣었다(2026-09-06). 본문에 길이 상한(20,000자)이 처음 생겼다.
    ("expectation_extraction", "2"): "28b977e7586f2bd96abfa6ba41c8ff310dd249cc7034a0651fb4eb7cd05f1699",
    # 판 5(평가)·판 3(추출)은 출력 형식을 YAML에서 빼고 응답 모델의 description으로 옮긴 같은 날의
    # 두 번째 판이다(2026-09-06). 이 판부터 해시가 YAML과 응답 스키마를 함께 잰다.
    ("assessment", "5"): "3b832f6568db31e562fd47e12ef35b95a742b745304ff971ec26ced3002b9c40",
    ("expectation_extraction", "3"): "84489961e5afeac66cdd47f6df1403d9d05bfe57609f417675171af752ae32be",
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
    # 판 7은 관계 표에 `none` 관측이 실리기 시작한 판(2026-09-06, 설계 §8.10). **해시가 판 6과
    # 같다** — 전망 문장은 안 바뀌고 장후 관찰이 바뀌었는데, 그 결과로 가중치와
    # `recent_signs`가 달라져 전망 모델이 보는 것이 바뀐다. 판 6과 같은 이유로 판만 올린다.
    ("kospi_forecast", "7"): "7a9f50fd46b08a4b7e754c46ce38c455c5688dcab2acdac8e18f7a2003f06149",
    # 판 8은 출력 형식을 YAML에서 빼고 응답 모델의 description으로 옮겼다(2026-09-06, 판 7과
    # 같은 날 배포). 이 판부터 해시가 YAML과 스키마를 함께 잰다. 다른 흐름의 같은 날 판도 같다.
    ("kospi_forecast", "8"): "7f8e69b5bce171af2d968ea4e7aed3320c86cda36090f4b8f8eb5d83a91e1129",
    # 장후 관찰의 첫 판(2026-09-02). 관찰·새 메모·메모 판정 셋을 한 답에 낸다.
    ("kospi_review", "1"): "e7f0097f2e306984b759ec383d06c08630de98e8043ecb60c10f20f7d5e793f2",
    # 판 2는 모델이 요인을 고르지 않는다(2026-09-06, 설계 §8.10). 코드가 숫자 요인 15개 값을
    # 표로 주고 모델은 줄마다 `same`/`inverse`/`none`을 답한다. `factor_history`가 툴 목록에서
    # 빠지고 `unlisted_drivers`가 답에 늘었다.
    ("kospi_review", "2"): "2481d7fb62e70754805d431e6b510b1b57b2d850ef7df284835297314b11106e",
    ("kospi_review", "3"): "b2b7bdd9f45b980b636d25540ad5c0d8c252723c7ee9a83063bad9fa96a16653",
    # 급변 원인 분석의 첫 판(2026-09-04). `cause_kind` 앵커가 이 판의 핵심이다 — 앵커 없이
    # 물었을 때 두 모델이 갈렸고(gpt `unclear`, grok `confirmed`) 넣으니 둘 다 `unclear`로
    # 수렴했다. "수급은 경로이지 방아쇠가 아니다"가 그 한 줄이다.
    ("shock_cause", "1"): "38c44ee1eeb04cdd046bfa4e46d340a74724b188b205ffdcebb9f7ab68fa106f",
    # 판 2는 외부 글 읽기 규칙 조각을 넣고 문서·검색 결과를 `<외부자료>`로 감쌌다(2026-09-06).
    # `cause_text`에 "링크를 넣지 마라"가 늘었고 코드가 링크 있는 문장을 버린다.
    ("shock_cause", "2"): "fd907995c9a19e8d392dd5c21869494c028229cb24a6d9e4fa0c0d9ff2f8fd85",
    ("shock_cause", "3"): "d989fc87fc47f8788ba747563497f2dfdc3ce2da6260c2c7c2b38a1dcc9eb59c",
}

# 그 흐름이 `response_format`으로 강제하는 응답 모델. 스키마가 해시에 들어간다.
RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "assessment": Assessment,
    "expectation_extraction": ExtractionResponse,
    "kospi_forecast": ForecastAnswer,
    "kospi_review": ReviewAnswer,
    "shock_cause": CauseAnswer,
}


def prompt_hash(name: str) -> str:
    """YAML 바이트 뒤에 응답 스키마의 압축 JSON을 이어 SHA-256을 낸다."""
    schema = json.dumps(strict_json_schema(RESPONSE_MODELS[name]), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((PROMPT_ROOT / f"{name}.yaml").read_bytes() + schema.encode()).hexdigest()

# 현재 판을 어디서 읽는지. 표의 키와 대조하는 데만 쓴다.
PROMPT_VERSIONS: dict[str, str] = {
    "assessment": ASSESSMENT_PROMPT_VERSION,
    "expectation_extraction": EXPECTATION_PROMPT_VERSION,
    "kospi_forecast": KOSPI_PROMPT_VERSION,
    "kospi_review": KOSPI_REVIEW_PROMPT_VERSION,
    "shock_cause": SHOCK_CAUSE_PROMPT_VERSION,
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

    actual = prompt_hash(name)

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
    assert set(RESPONSE_MODELS) == set(PROMPT_VERSIONS)


def test_prompt_files_leave_the_output_shape_to_the_response_model():
    """출력 모양은 `Field(description=...)`이 말한다. YAML에 예시 JSON을 다시 적으면 둘이 어긋난다."""
    for path in PROMPT_ROOT.glob("*.yaml"):
        assert "출력 형식" not in path.read_text(encoding="utf-8"), f"{path.name}이 출력 형식을 적고 있다"
