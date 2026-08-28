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
from modules.causal.domain import PROMPT_VERSION as CAUSAL_PROMPT_VERSION
from modules.expectation.domain import PROMPT_VERSION as EXPECTATION_PROMPT_VERSION
from modules.prompt import PROMPT_ROOT
from modules.thesis.domain import PROMPT_VERSION as THESIS_PROMPT_VERSION
from modules.thesis.outcomes import NARRATIVE_PROMPT_VERSION

# 판이 붙는 프롬프트 파일. 흐름의 현재 판을 키에 함께 적는다.
# **문장을 고쳤으면 판을 올리고 이 해시도 같이 바꾼다. 둘을 같은 커밋에서 만진다.**
PROMPT_HASHES: dict[tuple[str, str], str] = {
    ("assessment", "3"): "98ca6e74ed7f241abeb7b4b459a86a3c22a459ff8189af6c063d92bc92ea8a79",
    ("causal_graph", "1"): "6d5516c5ee2d1140cf5abd044100738b1a2b25fd5db430598b6b077aa66639f1",
    # 판 2는 자리표시자에 들어가는 값의 모양이 바뀐 것이다(2026-08-28). 근거 후보를 대상
    # 코드로 안 좁히면서 문서 줄이 태그 목록을 싣게 됐다. YAML은 그대로라 해시가 1과 같다.
    ("causal_graph", "2"): "6d5516c5ee2d1140cf5abd044100738b1a2b25fd5db430598b6b077aa66639f1",
    # 판 3은 어휘 재사용에 조건을 달았다(2026-08-28). 사슬과 `reasoning`이 같은 말을 하는지가
    # 기존 이름을 고를지 새로 만들지의 판정 기준이라고 프롬프트가 직접 밝힌다.
    ("causal_graph", "3"): "88e6d3867e71d74f65ed5912c93dcdea8f4a52aa2d7bd56af9da5935846fb74e",
    # 판 4는 사건을 고르는 규칙을 더했다(2026-08-28). 대상 주 것을 새로 만드는 것이 기본이고,
    # 같은 일을 날짜만 달리해 쪼개지 않는다. 후보 창은 코드가 1주로 좁힌다.
    ("causal_graph", "4"): "819558a38ac3106586e7359cf42297079722145c4f5bad38da46c6650733ab0d",
    # 판 5는 `confidence`를 가르는 기준이 근거를 읽었는지라고 밝혔다(2026-08-28).
    # 판 4 실행이 경로 서른넷을 전부 `plausible`로 냈다.
    ("causal_graph", "5"): "0711c09f0eef35133b476949efccdbe8e47af45b980f776046f68854c6a5547d",
    # 판 6은 툴 셋을 붙였다(2026-08-28). 프롬프트가 언제 무엇을 부를지 안내한다.
    ("causal_graph", "6"): "a3c835998eb65bfa4ed4449e7f4989daf451d3985eaded84e2f1bad6bb3d0886",
    # 판 7은 넷째 툴 `macro_indicators`를 안내한다(2026-08-28). 대상 아홉에 없는 매크로
    # 지표를 모델이 값으로 볼 수 있게 됐다.
    ("causal_graph", "7"): "994dea4f799cf080666e5a233fe7903dea4e01e4272809b8bdf5f93cccb2bf61",
    # 판 8은 기사 숫자를 근거로 쓰지 못하게 막았다(2026-08-28). 문서는 요약만 있고 원문이
    # 비어 있어 그 숫자를 되짚을 수 없다.
    ("causal_graph", "8"): "83d485c7ec318f95d85751bcac7c251d44f189a890d40afb96a590e4834205bb",
    ("expectation_extraction", "1"): "7108eab56e598ff642aeb7269f0f07ab9ef21707798202447db6a6b0a3b52a41",
    # 판 8은 문장이 아니라 **자리표시자에 들어가는 값의 모양**이 바뀐 것이다(2026-08-27).
    # 관측 상태·과거 추론 JSON에서 들여쓰기를 뺐다 — 모델이 보는 입력이 달라지므로 판을
    # 가르지만 YAML은 그대로라 해시가 7과 같다. **같은 해시가 두 판에 걸린 것이 정상이다.**
    ("thesis_generation", "7"): "ba0a741a06869b16aa3a439ebe56c33cdfa8b4084366c3d6ec183e8d5c426154",
    ("thesis_generation", "8"): "ba0a741a06869b16aa3a439ebe56c33cdfa8b4084366c3d6ec183e8d5c426154",
    # 판 9는 교정 문구 하나(`variants.repair_short_answer`)가 늘어 해시가 갈린다.
    # 문장보다 결과가 달라진 판이다 — 대상이 모자란 답을 한 번 다시 묻는다.
    ("thesis_generation", "9"): "09339dfae4c0ea0c32fe751b0b29d5b5becd9d431e5cefe158df59935879043f",
    # 판 10은 출력 형식 스켈레톤의 크기 자리표시자가 `0.0`에서 `null`로 바뀌어 해시가 갈린다.
    # `0.0`을 그대로 베낀 답이 매번 임계에서 버려지고 있었다.
    ("thesis_generation", "10"): "0c6aae2d149ccc8845d521cb87c8d60c633a8cc98104f8d7525d4dd13be01e91",
    # 판 11은 교정 문구가 **버린 사유**를 싣는다(2026-08-27 intraday: 세 이유가 모두 비어
    # 전부 버려졌는데, 사유 없는 교정을 받은 모델이 같은 답을 다시 냈다).
    ("thesis_generation", "11"): "91166bc5f5e98244b1759f031763ba2828b2fc7afd0ed50ae364ef8fc3da6672",
    # 판 12는 `prob_flat` 캘리브레이션이다(2026-08-28). 채점 84건에서 모델 평균 0.31,
    # 실제 13%였다. "창이 짧으면 flat이 잦다"는 문장을 실측으로 뒤집고(장중 12~25%,
    # 하루 13%) 기준선의 두 배를 상한으로 못박았다.
    ("thesis_generation", "12"): "26997dc850158ca01af32d66269b30f74c766e2e62ee70da68ce6d1ece576be1",
    # 판 13은 **같은 해시다.** YAML 문장은 그대로이고 `macro_changes`가 돌려주는 행에서
    # 국내 지수가 빠졌다. 모델이 보는 글자가 달라져 판을 가른다.
    ("thesis_generation", "13"): "26997dc850158ca01af32d66269b30f74c766e2e62ee70da68ce6d1ece576be1",
    # 판 14는 `## 크기` 절을 통째로 바꿔 해시가 갈린다. 기준선이 `typical_move`로 옮겼고
    # 브레이크가 대칭이 됐으며 오차 폭 두 칸이 붙었다.
    ("thesis_generation", "14"): "7146c65c9150ff8fd600792e9a7f4c4c7c57e12de08911562720379de944c6e8",
    ("thesis_narrative", "2"): "1baea1c554c90619576036db58ad42d2a1e24052fc8ab982978e605c6e696b8b",
    # 판 3은 **같은 해시다.** YAML 문장은 그대로이고 자리표시자에 들어가는 값의 줄 수만
    # 늘었다(예측의 축, 밴드 적중). 모델이 보는 글자가 달라져 판을 가른다.
    ("thesis_narrative", "3"): "1baea1c554c90619576036db58ad42d2a1e24052fc8ab982978e605c6e696b8b",
    # 판 4는 절 둘이 붙어 해시가 갈린다(16단계). `## 조사 규칙`이 무엇을 부르고 나서 답할지를
    # 열거하고, `## 표기`가 본문의 `(ref: ...)`를 막고 숫자 표기를 원문보다 앞세운다.
    # **모델 교체(`narration_model`)와 같은 판이라 둘의 효과가 분리되지 않는다.**
    ("thesis_narrative", "4"): "fded1e90fc368d5f1e126f67aa76474bfad84e5ad9667104ff7735e481260a92",
}

# 현재 판을 어디서 읽는지. 표의 키와 대조하는 데만 쓴다.
PROMPT_VERSIONS: dict[str, str] = {
    "assessment": ASSESSMENT_PROMPT_VERSION,
    "causal_graph": CAUSAL_PROMPT_VERSION,
    "expectation_extraction": EXPECTATION_PROMPT_VERSION,
    "thesis_generation": THESIS_PROMPT_VERSION,
    "thesis_narrative": NARRATIVE_PROMPT_VERSION,
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
