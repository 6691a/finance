"""Slack 정기 리포트가 쓰는 조회·렌더링·요약.

파트마다 파일 하나다. `market.py`, `documents.py`, `ops.py`가 각각
`collect_summary`, `render_blocks`, `render_text`, `comment_input` 네 함수를 갖는다.
`comment.py`는 세 파트가 함께 쓰는 LLM 요약이고 `table.py`는 고정폭 표다.

DAG는 이 함수들을 순서대로 부르기만 한다. 이 패키지는 Airflow를 import하지 않는다.
"""
