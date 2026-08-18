"""Slack 정기 리포트가 쓰는 조회·렌더링·요약.

파트마다 파일 하나다. `market.py`, `documents.py`, `ops.py`가 각각
`collect_summary`, `render_blocks`, `render_text`를 갖는다. LLM에 넘길 입력을 만드는 함수는
그 파트가 모델을 어디에 쓰느냐에 따라 다르다. 시장 리포트는 요약을 붙이므로 `comment_input`,
문서 리포트는 읽을 것을 고르게 하므로 `pick_input`이고, 운영 리포트는 모델을 부르지 않아
없다.

`comment.py`는 시장 리포트의 LLM 요약, `picks.py`는 문서 리포트의 LLM 선별,
`table.py`는 고정폭 표다.

DAG는 이 함수들을 순서대로 부르기만 한다. 이 패키지는 Airflow를 import하지 않는다.
"""
