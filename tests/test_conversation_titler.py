"""Unit tests for conversation auto-title generation (XMGPLAT-10380).

Covers ``generate_conversation_title``:

  (a) returns the truncated first-user-message fallback when ``llm_client``
      is ``None``;
  (b) calls the LLM with the configured system prompt and returns its
      (sanitized) output on success;
  (c) strips surrounding quotes and collapses whitespace/newlines from LLM
      output;
  (d) falls back to the truncated first message when the LLM call raises;
  (e) falls back to a generic default when there are no user messages at all;
  (f) caps overlong LLM output at the safety-net length.
"""

from autolangchat.conversation_titler import _TITLE_SYSTEM_PROMPT, generate_conversation_title


class _FakeAIMessage:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, response_content):
        self.response_content = response_content
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return _FakeAIMessage(self.response_content)


class _FailingLLM:
    async def ainvoke(self, messages):
        raise RuntimeError("boom")


def _messages():
    return [
        {"role": "user", "content": "How do I configure the SSO okta integration for our chat plugin?"},
        {"role": "assistant", "content": "You need to set AUTOCHAT_SSO_PROVIDER=okta and a few env vars..."},
    ]


def _expected_fallback():
    first_user_content = _messages()[0]["content"]
    return first_user_content[:50] + "..."


async def test_returns_fallback_when_llm_client_is_none():
    title = await generate_conversation_title(None, _messages())
    assert title == _expected_fallback()


async def test_generates_title_from_llm_and_uses_system_prompt():
    llm = _FakeLLM("Configuring Okta SSO Integration")
    title = await generate_conversation_title(llm, _messages())
    assert title == "Configuring Okta SSO Integration"

    assert len(llm.calls) == 1
    lc_messages = llm.calls[0]
    assert lc_messages[0].content == _TITLE_SYSTEM_PROMPT


async def test_strips_quotes_and_collapses_whitespace():
    llm = _FakeLLM('"Okta   SSO\nSetup Guide"')
    title = await generate_conversation_title(llm, _messages())
    assert title == "Okta SSO Setup Guide"


async def test_handles_structured_claude_style_content_blocks():
    llm = _FakeLLM([{"type": "text", "text": "Okta SSO Configuration Help"}])
    title = await generate_conversation_title(llm, _messages())
    assert title == "Okta SSO Configuration Help"


async def test_falls_back_when_llm_raises():
    title = await generate_conversation_title(_FailingLLM(), _messages())
    assert title == _expected_fallback()


async def test_falls_back_to_default_when_no_user_messages():
    title = await generate_conversation_title(_FakeLLM("whatever"), [])
    assert title == "New Conversation"


async def test_caps_overlong_llm_output():
    llm = _FakeLLM("A" * 200)
    title = await generate_conversation_title(llm, _messages())
    assert len(title) <= 83  # 80-char cap + "..."
    assert title.endswith("...")
