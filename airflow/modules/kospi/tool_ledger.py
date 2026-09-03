"""툴 호출 원장. **툴박스가 쥔 상태 중 하나를 클래스로 뗀 것이다.**

기록만 쌓고 **DB에는 쓰지 않는다** — 읽기 전용 툴 셋이라는 성격을 유지하고 저장 시점은
부르는 쪽(`kospi/store.py`)이 정한다. `KospiToolbox`가 하나를 소유하고 다섯을 그대로
넘겨준다(`tool_calls`·`round_count`·`begin_round`·`finish_round`·`close_open_records`).

**툴 호출을 남기는 자리는 둘이다.** 함수 래퍼만으로는 부족하다 — 모르는 툴과 Pydantic 인자
오류는 원래 함수에 도달하기 전에 `ToolNode`가 오류 `ToolMessage`로 바꾸기 때문이다. 그래서
요청 shell(`begin_round`)과 실제 실행(`record`)을 따로 잡고, 마지막에 `finish_round`가
`ToolMessage`로 둘을 맞춘다.

**툴 이름을 들고 있지 않는다.** 어떤 이름이 유효한지는 툴박스가 알고, 이 원장이 그것을 쓰는
자리는 `finish_round` 하나뿐이라 인자로 받는다. 뒤로 참조를 만들면 원장 하나를 테스트하는 데
툴박스가 필요해진다 — 그것이 이 파일을 만든 이유와 정반대다.
"""

import time
from collections.abc import Callable, Container, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from modules.kospi.domain import ToolCallErrorKind, ToolCallRecord, ToolLimitExceeded


def message_text(message: ToolMessage) -> str:
    """`ToolMessage` 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)


class ToolCallLedger:
    """대화 하나의 툴 호출 기록. 연결도 기준 시각도 모른다."""

    def __init__(self) -> None:
        self._records: list[ToolCallRecord] = []
        self._by_call_id: dict[str, ToolCallRecord] = {}
        self._rounds = 0

    @property
    def calls(self) -> tuple[ToolCallRecord, ...]:
        """이번 대화에서 기록한 툴 호출 전부. 부르는 쪽이 `kospi_tool_call`로 저장한다."""
        return tuple(self._records)

    @property
    def round_count(self) -> int:
        """조사 왕복 수. 실패한 대화는 그래프 최종 상태를 못 받아 이 값이 유일한 출처다."""
        return self._rounds

    def begin_round(self, tool_calls: Sequence[dict[str, Any]]) -> None:
        """모델이 요청한 tool_call마다 빈 기록을 연다.

        여기서 잡는 것은 **모델이 실제로 보낸 것**이다 — 이름, 검증 전 인자, 제공처 call id,
        그리고 요청을 등록한 시각. 실행 결과는 래퍼가, 모델에게 돌아갔는지는
        `finish_round`가 채운다.
        """
        self._rounds += 1
        requested_at = datetime.now(UTC)
        for call in tool_calls:
            call_id = str(call.get("id") or "")
            record = ToolCallRecord(
                seq=len(self._records) + 1,
                round_no=self._rounds,
                tool_call_id=call_id,
                tool_name=str(call.get("name") or ""),
                arguments=dict(call.get("args") or {}),
                requested_at=requested_at,
            )
            self._records.append(record)
            self._by_call_id[call_id] = record

    def finish_round(self, messages: Sequence[BaseMessage], *, known_tools: Container[str]) -> None:
        """`ToolNode`가 돌려준 `ToolMessage`로 그 라운드의 기록을 닫는다.

        **여기서만 알 수 있는 것이 둘이다.**

        - 함수에 진입하지 못한 실패(모르는 툴, 인자 검증). 래퍼가 못 보므로 이 자리가
          아니면 그 호출은 영영 빈 기록으로 남는다. 둘을 가르는 것이 `known_tools`다.
        - `delivered` — 결과가 모델 대화에 실제로 돌아갔나. sibling 하나가 처리되지 않은
          예외를 올리면 `ToolNode`는 나머지 결과를 **버린다.** 그런데 sync 경로가
          `executor.map`이라 이미 시작된 sibling은 취소되지 않고 끝까지 돈다 — 래퍼가
          결과를 다 채운 행이 남는다. 그것은 오류가 아니라 "모델만 못 봤다"이고,
          인용 분석이 정확히 그 구분 위에 선다.
        """
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            record = self._by_call_id.get(str(message.tool_call_id))
            if record is None:
                continue
            record.delivered = True
            if record.result is not None:
                continue
            # 여기 오는 것은 함수에 진입하지 못했거나(unknown tool·인자 검증) 래퍼가 이미
            # 예외를 남긴 경우다. 어느 쪽이든 모델이 읽은 문자열은 이 본문이라 그것을 담는다.
            if record.error_kind is None:
                record.error_kind = (
                    ToolCallErrorKind.UNKNOWN_TOOL
                    if record.tool_name not in known_tools
                    else ToolCallErrorKind.VALIDATION
                )
            record.error = message_text(message)

    def close_open_records(self) -> None:
        """끝나고도 결과·오류가 없는 기록을 닫는다. 실행조차 못 한 sibling이다.

        워커가 포화됐을 때(`max_concurrency` 지정, 또는 호출 수 > `min(32, cpu+4)`) sibling
        하나의 예외가 아직 시작 안 한 것들을 취소한다. 그 행은 `result`도 `error`도 없어
        DB CHECK(둘 중 하나는 있어야 한다)를 어긴다 — 여기서 닫아야 저장할 수 있다.
        """
        for record in self._records:
            if record.result is None and record.error is None:
                record.error_kind = ToolCallErrorKind.CANCELLED
                record.error = "sibling 실패로 실행되지 않았다"

    def record(self, name: str, func: Callable[..., str]) -> Callable[..., str]:
        """툴 함수 하나를 기록으로 감싼다. 툴 셋이 이 래퍼 하나를 지난다.

        **`**kwargs`로 받는다.** 그러면 `StructuredTool`이 `args_schema`와 시그니처를
        대조하지 않아 생기는 실패 모드(스키마에만 있는 인자 → 호출 시 `TypeError` →
        `ToolInvocationError`로 감싸이지 않아 태스크 사망)가 구조적으로 사라진다.
        개별 시그니처로 되돌리지 않는다.

        **예외는 기록한 뒤 다시 올린다.** `ToolLimitExceeded`는 `ToolNode`가 오류
        `ToolMessage`로 바꿔야 하고, DB 오류는 태스크를 죽여야 한다.
        """

        def call(**kwargs: Any) -> str:
            record = self._by_call_id.get(str(kwargs.pop("tool_call_id", "") or ""))
            started = time.perf_counter()
            try:
                body = func(**kwargs)
            except ToolLimitExceeded as error:
                self._close_record(record, kwargs, started, error=error, kind=ToolCallErrorKind.LIMIT)
                raise
            # 넓게 잡되 **반드시 다시 올린다.** 여기서 잡는 이유는 기록 하나뿐이고,
            # 삼키면 DB 끊김이 "결과 없음"으로 위장된다.
            except Exception as error:
                self._close_record(record, kwargs, started, error=error, kind=ToolCallErrorKind.EXECUTION)
                raise
            self._close_record(record, kwargs, started, result=body)
            return body

        call.__name__ = name
        call.__doc__ = func.__doc__
        return call

    @staticmethod
    def _close_record(
        record: ToolCallRecord | None,
        kwargs: dict[str, Any],
        started: float,
        *,
        result: str | None = None,
        error: BaseException | None = None,
        kind: ToolCallErrorKind | None = None,
    ) -> None:
        """실행이 끝난 기록에 실제 인자·소요·결과를 채운다. `delivered`는 아직 모른다."""
        if record is None:
            return
        record.validated_arguments = dict(kwargs)
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        if error is not None:
            record.error_kind = kind
            record.error = str(error)
            return
        record.result = result
        record.result_chars = len(result or "")
