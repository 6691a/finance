""".claude/skills와 .agents/skills의 정합, 그리고 규약 문서의 방아쇠 표.

규칙 넷(`writing-collectors`·`writing-llm-flows`·`writing-migrations`·`table-conventions`)을
2026-08-28에 `.claude/CLAUDE.md`에서 스킬로 내렸다. **스킬은 불려야 존재한다** — 방아쇠가
없거나 이름이 어긋나면 그 규칙은 사라진 것과 같다. 이 파일이 그 두 가지를 잡는다.

`.claude/skills`(Claude Code)와 `.agents/skills`(Codex)는 같은 파일을 두 벌 둔다.
심볼릭 링크가 아니라 사본이라 한쪽만 고치면 두 도구가 다른 규칙을 본다 —
`test_api_stack.py`가 compose 두 스택을 대조하는 것과 같은 이유다.
"""

import re
from pathlib import Path

CLAUDE_SKILLS = Path(".claude/skills")
AGENT_SKILLS = Path(".agents/skills")
CONVENTION_DOCS = (Path(".claude/CLAUDE.md"), Path(".codex/AGENTS.md"))

# 규약 문서에서 내려온 스킬. graphify처럼 외부에서 설치된 것은 대상이 아니다.
EXTRACTED = (
    "writing-collectors",
    "writing-llm-flows",
    "writing-migrations",
    "table-conventions",
)


def skill_names(root: Path) -> set[str]:
    return {path.parent.name for path in root.glob("*/SKILL.md")}


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path}에 YAML frontmatter가 없다"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def test_extracted_skills_exist_in_both_trees() -> None:
    """내려온 넷은 두 트리에 모두 있어야 한다. 한쪽만 있으면 그 도구만 규칙을 잃는다."""
    for name in EXTRACTED:
        assert (CLAUDE_SKILLS / name / "SKILL.md").is_file(), f"{name}이 .claude/skills에 없다"
        assert (AGENT_SKILLS / name / "SKILL.md").is_file(), f"{name}이 .agents/skills에 없다"


def test_shared_skills_are_byte_identical() -> None:
    """두 트리에 같은 이름이 있으면 내용도 같아야 한다."""
    for name in sorted(skill_names(CLAUDE_SKILLS) & skill_names(AGENT_SKILLS)):
        claude = (CLAUDE_SKILLS / name / "SKILL.md").read_bytes()
        agent = (AGENT_SKILLS / name / "SKILL.md").read_bytes()
        assert claude == agent, f"{name}의 SKILL.md가 두 트리에서 다르다"


def test_skill_name_matches_its_directory() -> None:
    """frontmatter의 name이 폴더 이름과 달라지면 부르는 이름이 갈린다."""
    for name in EXTRACTED:
        fields = frontmatter(CLAUDE_SKILLS / name / "SKILL.md")
        assert fields.get("name") == name, f"{name}의 frontmatter name이 폴더와 다르다"


def test_description_stays_within_the_frontmatter_budget() -> None:
    """description은 스킬을 고르는 유일한 단서다. 상한을 넘으면 잘린다."""
    for name in EXTRACTED:
        description = frontmatter(CLAUDE_SKILLS / name / "SKILL.md").get("description", "")
        assert description.startswith("Use when"), f"{name}의 description이 'Use when'으로 시작하지 않는다"
        assert len(description) <= 500, f"{name}의 description이 {len(description)}자다"


def test_convention_docs_point_at_every_extracted_skill() -> None:
    """방아쇠가 빠지면 그 규칙은 없는 것과 같다. 두 규약 문서 모두 이름을 적어야 한다."""
    for doc in CONVENTION_DOCS:
        text = doc.read_text(encoding="utf-8")
        for name in EXTRACTED:
            assert f"`{name}`" in text, f"{doc}에 {name} 방아쇠가 없다"


def test_convention_docs_do_not_keep_a_second_copy() -> None:
    """규칙을 스킬로 내렸으면 규약 문서에 절 제목이 남아 있으면 안 된다.

    남기면 목록이 두 벌이 되고 반드시 어긋난다 — `docs/README.md`가
    `implementation-gaps.md`를 두 벌 관리하지 않는 것과 같은 판단이다.
    """
    moved_headings = (
        "## 수집기 작성",
        "## LLM 코드",
        "## 테이블 규칙",
        "## 마이그레이션 라우팅",
        "## 마이그레이션 작성",
    )
    for doc in CONVENTION_DOCS:
        lines = doc.read_text(encoding="utf-8").splitlines()
        for heading in moved_headings:
            assert not any(line.startswith(heading) for line in lines), f"{doc}에 {heading} 절이 남아 있다"
