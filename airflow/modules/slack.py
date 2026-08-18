"""Slack 채널로 메시지 한 건을 보낸다.

호출은 `slack_sdk.WebClient`다. 손으로 HTTP를 치지 않는 이유는 LangChain을 쓰는 이유와 같다.
요청 조립, 응답 파싱, 오류 타입이 이미 있는데 다시 만들 값어치가 없다.

**재시도 핸들러를 붙이지 않는다.** SDK가 먼저 재시도하면 태스크 타임아웃 안에서 몇 번을
불렀는지 로그와 어긋난다. LLM 클라이언트를 `max_retries=0`으로 만드는 것과 같은 이유이고,
재시도는 Airflow가 한다.

**Slack은 실패를 HTTP 상태가 아니라 본문 `ok: false`와 `error` 코드로 알린다.** 그래서
`EcosResultError`와 같은 자리에 `SlackError`가 선다. 여기서는 종류만 가르고 재시도 여부는
DAG가 정한다.

이 모듈은 Airflow를 import하지 않는다. import하면 테스트가 배포 환경 없이 돌지 않는다.
"""

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError, SlackClientError

logger = logging.getLogger(__name__)

# 한 번의 전송을 기다리는 시간. 이보다 오래 걸리면 그 실행은 포기하고 Airflow가 다시 집는다.
REQUEST_TIMEOUT_SECONDS = 30

# 잠시 뒤 다시 부르면 될 실패. 나머지 코드는 설정이 틀린 것이라 재시도해도 같은 결과다.
RETRYABLE_API_ERRORS = frozenset(
    {
        "ratelimited",
        "internal_error",
        "service_unavailable",
        "fatal_error",
        "message_limit_exceeded",
    }
)


class SlackError(RuntimeError):
    """Slack이 거절했고 다시 불러도 같은 결과다."""


def post_message(
    token: SecretStr,
    channel: str,
    *,
    text: str,
    blocks: Sequence[dict[str, Any]] | None = None,
) -> str:
    """메시지 한 건을 보내고 `ts`를 돌려준다.

    `text`는 블록을 못 그리는 자리(알림, 검색 결과)에 뜨는 대체 문구라 항상 채운다.
    """
    client = WebClient(
        token=token.get_secret_value(),
        timeout=REQUEST_TIMEOUT_SECONDS,
        # 재시도는 Airflow가 한다. 위 모듈 docstring 참고.
        retry_handlers=[],
    )
    payload: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = list(blocks)

    try:
        response = client.chat_postMessage(**payload)
    except SlackApiError as error:
        raise classify(error) from error
    except SlackClientError as error:
        # 응답이 없는 실패. 코드가 없어 가를 수 없으니 재시도할 값어치가 있는 쪽으로 둔다.
        raise ConnectionError(f"slack request failed: {error}") from error

    timestamp = response.get("ts")
    if not timestamp:
        raise SlackError(f"slack accepted the message but returned no ts: {channel}")
    return str(timestamp)


def classify(error: SlackApiError) -> Exception:
    """Slack 예외를 DAG가 아는 종류로 바꾼다. 토큰은 여기 실리지 않는다."""
    response = error.response
    code = response.get("error") if response is not None else None
    if code in RETRYABLE_API_ERRORS:
        return ConnectionError(f"slack is unavailable: {code}")
    return SlackError(f"slack rejected the message: {code or error}")
