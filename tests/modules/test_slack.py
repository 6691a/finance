import pytest
from pydantic import SecretStr
from slack_sdk.errors import SlackApiError, SlackClientError

from modules import slack

TOKEN = SecretStr("xoxb-super-secret")


class FakeWebClient:
    """`WebClient` 자리에 끼운다. 실제 호출은 하지 않는다."""

    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.response = response or {"ok": True, "ts": "1755500000.000100"}
        self.error = error
        self.calls: list[dict] = []
        self.kwargs: dict = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    def files_upload_v2(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def api_error(code: str) -> SlackApiError:
    return SlackApiError(f"failed: {code}", {"ok": False, "error": code})


def test_sends_channel_text_and_blocks(monkeypatch):
    client = FakeWebClient()
    monkeypatch.setattr(slack, "WebClient", client)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "본문"}}]

    ts = slack.SlackClient(TOKEN).post_message("C123", text="한 줄 요약", blocks=blocks)

    assert ts == "1755500000.000100"
    assert client.calls[0] == {"channel": "C123", "text": "한 줄 요약", "blocks": blocks}


def test_blocks_are_optional(monkeypatch):
    client = FakeWebClient()
    monkeypatch.setattr(slack, "WebClient", client)

    slack.SlackClient(TOKEN).post_message("C123", text="한 줄")

    assert "blocks" not in client.calls[0]


def test_sdk_retry_is_off(monkeypatch):
    """재시도는 Airflow가 한다. SDK가 먼저 재시도하면 호출 횟수가 로그와 어긋난다."""
    client = FakeWebClient()
    monkeypatch.setattr(slack, "WebClient", client)

    slack.SlackClient(TOKEN).post_message("C123", text="한 줄")

    assert client.kwargs["retry_handlers"] == []


@pytest.mark.parametrize(
    "code",
    ["ratelimited", "internal_error", "service_unavailable", "fatal_error", "message_limit_exceeded"],
)
def test_transient_codes_are_retryable(monkeypatch, code):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error(code)))

    with pytest.raises(ConnectionError):
        slack.SlackClient(TOKEN).post_message("C123", text="한 줄")


@pytest.mark.parametrize("code", ["invalid_auth", "channel_not_found", "not_in_channel", "invalid_blocks"])
def test_settings_errors_are_not_retryable(monkeypatch, code):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error(code)))

    with pytest.raises(slack.SlackError):
        slack.SlackClient(TOKEN).post_message("C123", text="한 줄")


def test_network_failure_is_retryable(monkeypatch):
    """응답 자체가 없는 실패. 코드로 가를 수 없으므로 재시도한다."""
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=SlackClientError("connection reset")))

    with pytest.raises(ConnectionError):
        slack.SlackClient(TOKEN).post_message("C123", text="한 줄")


def test_token_never_reaches_the_exception(monkeypatch):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error("invalid_auth")))

    with pytest.raises(slack.SlackError) as raised:
        slack.SlackClient(TOKEN).post_message("C123", text="한 줄")

    assert TOKEN.get_secret_value() not in str(raised.value)


def test_missing_timestamp_is_an_error(monkeypatch):
    """Slack이 ok를 줬는데 ts가 없으면 우리가 아는 응답이 아니다."""
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(response={"ok": True}))

    with pytest.raises(slack.SlackError):
        slack.SlackClient(TOKEN).post_message("C123", text="한 줄")


def test_upload_returns_the_file_id_without_sharing(monkeypatch):
    """채널을 넘기지 않는다. 파일은 메시지 블록이 `slack_file`로 참조한다."""
    client = FakeWebClient(response={"ok": True, "files": [{"id": "F0AAA"}]})
    monkeypatch.setattr(slack, "WebClient", client)

    file_id = slack.SlackClient(TOKEN).upload_file(filename="chart.png", title="차트", content=b"\x89PNG")

    assert file_id == "F0AAA"
    assert client.calls[0] == {"file": b"\x89PNG", "filename": "chart.png", "title": "차트"}
    assert client.kwargs["retry_handlers"] == []


def test_upload_without_a_file_id_is_an_error(monkeypatch):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(response={"ok": True, "files": []}))

    with pytest.raises(slack.SlackError):
        slack.SlackClient(TOKEN).upload_file(filename="chart.png", title="차트", content=b"x")


def test_upload_errors_are_classified_like_messages(monkeypatch):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error("ratelimited")))
    with pytest.raises(ConnectionError):
        slack.SlackClient(TOKEN).upload_file(filename="chart.png", title="차트", content=b"x")

    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error("invalid_auth")))
    with pytest.raises(slack.SlackError):
        slack.SlackClient(TOKEN).upload_file(filename="chart.png", title="차트", content=b"x")
