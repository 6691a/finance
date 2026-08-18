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


def api_error(code: str) -> SlackApiError:
    return SlackApiError(f"failed: {code}", {"ok": False, "error": code})


def test_sends_channel_text_and_blocks(monkeypatch):
    client = FakeWebClient()
    monkeypatch.setattr(slack, "WebClient", client)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "본문"}}]

    ts = slack.post_message(TOKEN, "C123", text="한 줄 요약", blocks=blocks)

    assert ts == "1755500000.000100"
    assert client.calls[0] == {"channel": "C123", "text": "한 줄 요약", "blocks": blocks}


def test_blocks_are_optional(monkeypatch):
    client = FakeWebClient()
    monkeypatch.setattr(slack, "WebClient", client)

    slack.post_message(TOKEN, "C123", text="한 줄")

    assert "blocks" not in client.calls[0]


def test_sdk_retry_is_off(monkeypatch):
    """재시도는 Airflow가 한다. SDK가 먼저 재시도하면 호출 횟수가 로그와 어긋난다."""
    client = FakeWebClient()
    monkeypatch.setattr(slack, "WebClient", client)

    slack.post_message(TOKEN, "C123", text="한 줄")

    assert client.kwargs["retry_handlers"] == []


@pytest.mark.parametrize(
    "code",
    ["ratelimited", "internal_error", "service_unavailable", "fatal_error", "message_limit_exceeded"],
)
def test_transient_codes_are_retryable(monkeypatch, code):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error(code)))

    with pytest.raises(ConnectionError):
        slack.post_message(TOKEN, "C123", text="한 줄")


@pytest.mark.parametrize("code", ["invalid_auth", "channel_not_found", "not_in_channel", "invalid_blocks"])
def test_settings_errors_are_not_retryable(monkeypatch, code):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error(code)))

    with pytest.raises(slack.SlackError):
        slack.post_message(TOKEN, "C123", text="한 줄")


def test_network_failure_is_retryable(monkeypatch):
    """응답 자체가 없는 실패. 코드로 가를 수 없으므로 재시도한다."""
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=SlackClientError("connection reset")))

    with pytest.raises(ConnectionError):
        slack.post_message(TOKEN, "C123", text="한 줄")


def test_token_never_reaches_the_exception(monkeypatch):
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(error=api_error("invalid_auth")))

    with pytest.raises(slack.SlackError) as raised:
        slack.post_message(TOKEN, "C123", text="한 줄")

    assert TOKEN.get_secret_value() not in str(raised.value)


def test_missing_timestamp_is_an_error(monkeypatch):
    """Slack이 ok를 줬는데 ts가 없으면 우리가 아는 응답이 아니다."""
    monkeypatch.setattr(slack, "WebClient", FakeWebClient(response={"ok": True}))

    with pytest.raises(slack.SlackError):
        slack.post_message(TOKEN, "C123", text="한 줄")
