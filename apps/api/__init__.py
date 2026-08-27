"""기록을 밖에서 읽는 **읽기 전용** JSON API.

**리소스는 늘어난다.** 지금 있는 것은 시장 추론 하나지만 서비스 이름·이미지·컨테이너는
`api`로 두고, 리소스 이름은 라우트와 컨테이너 provider(`thesis_service` 등)에만 둔다.

## 왜 있나 — Slack이 기록의 일부만 보여 준다

Slack이 보여 주는 것은 채택된 방향 하나의 확률·이유와 근거 제목 셋뿐이다. 세 방향 확률과
이유 전부, 인용 근거 전부(`evidence_kind`·`ref`·`detail`), 지평별 채점과 사후 해설,
프롬프트에 실린 관측 상태, 본 과거 추론, 그리고 그 판단을 만든 LLM 대화는 전부 DB에만
있다. 그것들을 보는 자리가 여기다.

**Slack에 더 싣는 것은 답이 아니다.** 읽는 사람이 다르다 — 그 메시지는 오늘 시장을 보는
사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다(`thesis_render.render_blocks`가
같은 판단을 적어 뒀다).

## 읽기 전용을 연결 층에서 강제한다

쓰기 라우트를 안 만드는 것으로 그치지 않는다. `main.py`의 상수 `DB_ALIAS`가 가리키는
별칭이 `read_only`가 아니면 **시작을 거부한다**. `apps/core/database.py`의 `_connect_args_for`가
그 연결에 `default_transaction_read_only = on`을 걸어, 실수로 쓰기가 들어가도 PostgreSQL이
거절한다. `apps/realtime/`가 대상 별칭에 `read_only: false`를 요구하는 가드의 정확한 반대다.

기록이 불변인 것이 이 도메인의 1원칙이라(`docs/analysis/market-thesis/README.md`) 사람이
행을 고치는 경로를 앱에 두지 않는다.

## 배포

`python -m apps.api.main`. 설정은 `config.yaml` 하나이고(`apps/realtime/`와 공유),
컨테이너 안 바인드 주소·포트만 `API_HOST`·`API_PORT`다 — 그 둘은 yaml이 아니라 환경변수다.
`config.yaml`은 컨테이너 여럿이 공유하는 접속 정보라 "이 컨테이너가 어디에 바인드하나"는
거기 속하지 않는다.

설계는 `docs/analysis/market-thesis/12-api.md`에 있다.
"""
