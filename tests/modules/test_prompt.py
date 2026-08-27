"""프롬프트 파일 로더의 계약.

프롬프트를 파이썬 밖으로 뺀 이유와 규칙은 `.claude/CLAUDE.md`의 "프롬프트는 코드가 아니다"에
있다. 여기서는 **파일이 깨졌을 때 조용히 통과하지 않는 것**을 지킨다.
"""

import pytest
import yaml

from modules.prompt import PROMPT_ROOT, PromptError, PromptSet, read_prompt

VALID = {"system": "너는 $role 이다.", "instruction": "$payload 를 읽어라.", "repair": "다시 하라."}


def write(tmp_path, monkeypatch, name: str, body: object) -> None:
    monkeypatch.setattr("modules.prompt.PROMPT_ROOT", tmp_path)
    text = body if isinstance(body, str) else yaml.safe_dump(body, allow_unicode=True)
    (tmp_path / f"{name}.yaml").write_text(text, encoding="utf-8")


def test_a_prompt_file_is_read_and_validated(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "sample", VALID)

    prompts = read_prompt("sample")

    assert prompts.system.startswith("너는")
    assert prompts.repair == "다시 하라."


def test_placeholders_are_filled(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "sample", VALID)

    assert read_prompt("sample").render("system", role="편집자") == "너는 편집자 이다."


def test_a_missing_value_fails_instead_of_reaching_the_model(tmp_path, monkeypatch):
    """자리표시자가 그대로 모델에게 나가는 것보다 태스크가 죽는 편이 낫다."""
    write(tmp_path, monkeypatch, "sample", VALID)

    with pytest.raises(PromptError):
        read_prompt("sample").render("system")


def test_json_braces_survive_substitution():
    """치환이 `string.Template`이라 출력 예시의 중괄호와 부딪히지 않는다.

    `str.format`이면 여기서 `KeyError`가 난다. `assessment.py`가 "중괄호를 넣지 마라"라는
    규칙으로 막고 있는 문제를 이 방식은 아예 안 만든다.
    """
    prompts = PromptSet(system='형식: {"a": [{"b": ""}]} · $tail', instruction="x", repair="y")

    assert prompts.render("system", tail="끝") == '형식: {"a": [{"b": ""}]} · 끝'


def test_a_missing_file_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.prompt.PROMPT_ROOT", tmp_path)

    with pytest.raises(PromptError):
        read_prompt("nothing_here")


def test_a_broken_file_fails_loudly(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "sample", "system: [unclosed\n")

    with pytest.raises(PromptError):
        read_prompt("sample")


def test_a_missing_section_fails_loudly(tmp_path, monkeypatch):
    write(tmp_path, monkeypatch, "sample", {"system": "x", "instruction": "y"})

    with pytest.raises(Exception, match="repair"):
        read_prompt("sample")


def test_an_unknown_section_fails_loudly(tmp_path, monkeypatch):
    """오타 난 칸이 조용히 무시되면 그 문장은 영영 안 쓰인다."""
    write(tmp_path, monkeypatch, "sample", {**VALID, "systemm": "오타"})

    with pytest.raises(Exception, match="systemm"):
        read_prompt("sample")


def test_every_prompt_file_in_the_repository_loads():
    """파일을 늘릴 때마다 테스트를 늘리지 않아도 되게 훑는다."""
    files = sorted(PROMPT_ROOT.glob("*.yaml"))

    assert files, "modules/prompts/에 프롬프트 파일이 하나도 없다"
    for path in files:
        assert read_prompt(path.stem).system
