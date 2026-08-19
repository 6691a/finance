"""heartbeat 상태 파일과 docker healthcheck.

상태 파일은 관측용이지 데이터가 아니다 — 쓰기 실패가 수집을 멈추지 않는다.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_STATES = ("idle", "connecting", "ready", "degraded", "failed")
HEARTBEAT_STALE_SECONDS = 120.0


def write_heartbeat(path: Path, state: str, **extra: Any) -> None:
    """healthcheck가 읽는 상태 파일. 임시 파일에 쓰고 바꿔치기해 찢긴 읽기를 막는다."""
    if state not in HEARTBEAT_STATES:
        raise ValueError(f"unknown heartbeat state {state!r}")
    payload = {"state": state, "written_at": datetime.now(UTC).isoformat(), **extra}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False))
    temporary.replace(path)


def healthcheck(path: Path, now: datetime | None = None) -> int:
    """docker healthcheck 진입점. 0=건강, 1=이상."""
    now = now or datetime.now(UTC)
    try:
        payload = json.loads(path.read_text())
        state = payload["state"]
        written_at = datetime.fromisoformat(payload["written_at"])
    except (OSError, ValueError, KeyError):
        return 1
    if state not in HEARTBEAT_STATES or state == "failed":
        return 1
    if (now - written_at).total_seconds() > HEARTBEAT_STALE_SECONDS:
        return 1
    return 0


class Heartbeat:
    """상태 파일 쓰기를 카운터와 함께 감싼 것. 파일 쓰기 실패는 수집을 멈출 이유가
    아니므로 경고만 남긴다 — 상태 파일은 관측용이지 데이터가 아니다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.state = "idle"

    def update(self, state: str, **extra: Any) -> None:
        if state != self.state:
            # 전이만 남긴다. idle 유지 30초마다 한 줄씩 쌓이면 로그가 소음이 된다.
            logger.info("State %s -> %s", self.state, state)
        self.state = state
        try:
            write_heartbeat(self._path, state, **extra)
        except OSError:
            logger.warning("Heartbeat write failed at %s", self._path)
