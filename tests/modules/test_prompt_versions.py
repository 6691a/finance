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
from modules.prompt import PROMPT_ROOT
from modules.thesis.domain import PROMPT_VERSION as THESIS_PROMPT_VERSION
from modules.thesis.outcomes import NARRATIVE_PROMPT_VERSION

# 판이 붙는 프롬프트 파일. 흐름의 현재 판을 키에 함께 적는다.
# **문장을 고쳤으면 판을 올리고 이 해시도 같이 바꾼다. 둘을 같은 커밋에서 만진다.**
PROMPT_HASHES: dict[tuple[str, str], str] = {
    ("assessment", "3"): "98ca6e74ed7f241abeb7b4b459a86a3c22a459ff8189af6c063d92bc92ea8a79",
    ("expectation_extraction", "1"): "7108eab56e598ff642aeb7269f0f07ab9ef21707798202447db6a6b0a3b52a41",
    # 판 8은 문장이 아니라 **자리표시자에 들어가는 값의 모양**이 바뀐 것이다(2026-08-27).
    # 관측 상태·과거 추론 JSON에서 들여쓰기를 뺐다 — 모델이 보는 입력이 달라지므로 판을
    # 가르지만 YAML은 그대로라 해시가 7과 같다. **같은 해시가 두 판에 걸린 것이 정상이다.**
    ("thesis_generation", "7"): "ba0a741a06869b16aa3a439ebe56c33cdfa8b4084366c3d6ec183e8d5c426154",
    ("thesis_generation", "8"): "ba0a741a06869b16aa3a439ebe56c33cdfa8b4084366c3d6ec183e8d5c426154",
    ("thesis_narrative", "2"): "1baea1c554c90619576036db58ad42d2a1e24052fc8ab982978e605c6e696b8b",
}

# 현재 판을 어디서 읽는지. 표의 키와 대조하는 데만 쓴다.
PROMPT_VERSIONS: dict[str, str] = {
    "assessment": ASSESSMENT_PROMPT_VERSION,
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
