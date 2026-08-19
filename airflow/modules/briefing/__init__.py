"""Slack 정기 리포트가 쓰는 조회·렌더링·요약.

파트마다 파일 하나다. `market.py`, `documents.py`, `ops.py`가 각각
`collect_summary`, `render_blocks`, `render_text`를 갖는다. LLM은 문서 리포트만 쓴다 —
읽을 것을 고르게 하는 `pick_input`과 `picks.py`다. 시장 리포트의 LLM 요약(`comment.py`)은
2026-08-19에 뺐다. 표가 이미 말하는 것 이상을 쓰지 못했다. 운영 리포트도 모델을 부르지 않는다.

DAG는 이 함수들을 순서대로 부르기만 한다. 이 패키지는 Airflow를 import하지 않는다.
"""
