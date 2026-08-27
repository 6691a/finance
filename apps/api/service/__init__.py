"""행을 응답 계약으로 바꾸는 층. **계약만 안다.**

행이 어디서 왔는지 모르고 `apps/api/schemas/`의 모양으로 바꾼다. 그래서 Neo4j를 조회
원본으로 채택하면 리포지토리만 갈리고 여기와 계약은 그대로다. 반대로 응답 모양이
바뀌면 여기만 갈린다.

**모양을 바꾸는 함수는 모듈 수준 순수 함수다.** 세션도 리포지토리도 필요 없어 테스트가
직접 부를 수 있고, 클래스는 그것들을 순서대로 엮기만 한다.

**파일은 리소스 단위로 나눈다**(`apps/api/schemas/`와 같은 규칙). `common.py`는 그
서비스들이 공유하는 변환이고, `__init__.py`는 **재수출만** 한다.
"""

from apps.api.service.common import number
from apps.api.service.thesis import (
    ThesisReadService,
    build_detail,
    build_list,
    citation_of,
    llm_run_of,
    outcome_of,
    precedent_of,
    project_graph,
    summary_of,
    thesis_node_id,
)

__all__ = [
    "ThesisReadService",
    "build_detail",
    "build_list",
    "citation_of",
    "llm_run_of",
    "number",
    "outcome_of",
    "precedent_of",
    "project_graph",
    "summary_of",
    "thesis_node_id",
]
