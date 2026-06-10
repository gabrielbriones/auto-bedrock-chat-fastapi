"""Tests for WebSocketChatHandler._format_kb_context source labeling."""

from __future__ import annotations

from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler


def _handler() -> WebSocketChatHandler:
    """Create a bare handler instance without wiring any dependencies."""
    return WebSocketChatHandler.__new__(WebSocketChatHandler)


def _result(*, source: str | None = None, title: str | None = None, source_url: str | None = None) -> dict:
    return {
        "similarity_score": 0.85,
        "content": "Some relevant content.",
        "title": title,
        "source": source,
        "source_url": source_url,
    }


class TestFormatKbContextSourceLabeling:
    def test_feedback_source_gets_validated_corrections_label(self):
        handler = _handler()
        output = handler._format_kb_context([_result(source="feedback")])
        assert "[Learned from validated corrections]" in output
        assert "[Reference documentation]" not in output

    def test_non_feedback_source_gets_reference_documentation_label(self):
        handler = _handler()
        output = handler._format_kb_context([_result(source="confluence")])
        assert "[Reference documentation]" in output
        assert "[Learned from validated corrections]" not in output

    def test_no_source_field_omits_source_line(self):
        handler = _handler()
        output = handler._format_kb_context([_result(source=None)])
        assert "Source:" not in output

    def test_empty_source_string_omits_source_line(self):
        handler = _handler()
        output = handler._format_kb_context([_result(source="")])
        assert "Source:" not in output

    def test_raw_source_value_not_leaked(self):
        """The raw 'feedback' string must not appear verbatim in the output."""
        handler = _handler()
        output = handler._format_kb_context([_result(source="feedback")])
        # The label should replace the raw value, not append to it.
        assert "Source: feedback" not in output

    def test_other_raw_source_value_not_leaked(self):
        """Non-feedback raw source values should be replaced by the generic label."""
        handler = _handler()
        output = handler._format_kb_context([_result(source="my-internal-tool")])
        assert "Source: my-internal-tool" not in output
        assert "[Reference documentation]" in output

    def test_multiple_results_each_labeled_independently(self):
        handler = _handler()
        results = [
            _result(source="feedback", title="Correction article"),
            _result(source="confluence", title="Reference article"),
        ]
        output = handler._format_kb_context(results)
        assert "[Learned from validated corrections]" in output
        assert "[Reference documentation]" in output

    def test_empty_results_returns_empty_string(self):
        handler = _handler()
        assert handler._format_kb_context([]) == ""

    def test_source_url_still_included(self):
        handler = _handler()
        output = handler._format_kb_context([_result(source="feedback", source_url="https://example.com/doc")])
        assert "https://example.com/doc" in output

    def test_title_still_included(self):
        handler = _handler()
        output = handler._format_kb_context([_result(source="feedback", title="My KB Article")])
        assert "My KB Article" in output
