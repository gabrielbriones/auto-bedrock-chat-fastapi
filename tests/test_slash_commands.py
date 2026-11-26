"""Slash-command grammar and direct skill loading (``autolangchat.skills.slash``)."""

import pytest

from autolangchat.skills.models import Skill, SkillParameter
from autolangchat.skills.service import INVALID_SKILL_ARGUMENTS, SkillLoadError, SkillService
from autolangchat.skills.slash import (
    INVALID_COMMAND,
    SlashCommand,
    SlashCommandError,
    is_literal_slash,
    load_slash_command,
    parse_slash_command,
)


class TestGrammar:
    def test_bare_command(self):
        assert parse_slash_command("/triage") == SlashCommand(name="triage")

    def test_hyphenated_and_underscored_names(self):
        assert parse_slash_command("/job-triage_v2 a=1") == SlashCommand(name="job-triage_v2", params={"a": "1"})

    def test_bare_and_quoted_values(self):
        command = parse_slash_command('/triage job_id=j-1 note="value with spaces"')

        assert command.params == {"job_id": "j-1", "note": "value with spaces"}
        assert command.text == ""

    def test_empty_values(self):
        assert parse_slash_command('/triage a= b="" c=').params == {"a": "", "b": "", "c": ""}

    def test_equals_sign_inside_quoted_value(self):
        assert parse_slash_command('/triage q="a=b=c"').params == {"q": "a=b=c"}

    def test_equals_sign_inside_bare_value(self):
        assert parse_slash_command("/triage q=a=b").params == {"q": "a=b"}

    def test_unicode_values(self):
        assert parse_slash_command('/triage who=José note="naïve ☕ 日本"').params == {
            "who": "José",
            "note": "naïve ☕ 日本",
        }

    def test_backslash_escapes_in_quoted_values(self):
        command = parse_slash_command(r'/triage q="say \"hi\" \\ done" path="C:\\tmp"')

        assert command.params == {"q": 'say "hi" \\ done', "path": "C:\\tmp"}

    def test_keys_are_case_insensitive(self):
        assert parse_slash_command("/triage JOB_ID=j-1").params == {"job_id": "j-1"}

    def test_extra_tokens_are_free_text(self):
        command = parse_slash_command("/summarize lang=en  please keep it short b=2 ")

        assert command.params == {"lang": "en"}
        assert command.text == "please keep it short b=2"

    def test_free_text_only(self):
        assert parse_slash_command("/summarize  the whole log\nthanks") == SlashCommand(
            name="summarize", text="the whole log\nthanks"
        )

    def test_duplicate_keys_are_an_error(self):
        with pytest.raises(SlashCommandError) as error:
            parse_slash_command("/triage a=1 A=2")

        assert error.value.code == INVALID_COMMAND
        assert "a" in error.value.message

    def test_unterminated_quote_is_an_error(self):
        with pytest.raises(SlashCommandError):
            parse_slash_command('/triage q="never closed')

    def test_trailing_backslash_in_quote_is_an_error(self):
        with pytest.raises(SlashCommandError):
            parse_slash_command('/triage q="ends with \\')

    def test_quote_must_be_followed_by_whitespace(self):
        with pytest.raises(SlashCommandError):
            parse_slash_command('/triage q="a"b')

    @pytest.mark.parametrize(
        "text",
        [
            "//triage a=1",
            "/ triage",
            "/",
            "/usr/bin is missing",
            "/Triage a=1",
            "/100% sure",
            "use /triage to start",
            "and/or",
            "",
            "/" + "a" * 65 + " x=1",
        ],
    )
    def test_plain_text(self, text):
        assert parse_slash_command(text) is None

    def test_literal_slash(self):
        assert is_literal_slash("//triage")
        assert not is_literal_slash("/triage")
        assert not is_literal_slash("a //b")


class _Store:
    def __init__(self, *skills):
        self.skills = {skill.name: skill for skill in skills}

    async def get_skill(self, name):
        return self.skills.get(name)


def _service(*skills):
    store = _Store(*skills)
    return SkillService(lambda: store)


_TRIAGE = Skill(
    name="triage",
    display_name="Triage",
    instructions="Investigate {{job_id}}.",
    parameters=[SkillParameter(name="job_id", required=True)],
    revision=4,
)
_SUMMARIZE = Skill(
    name="summarize",
    display_name="Summarize",
    instructions="Summarize {{input}}.",
    parameters=[SkillParameter(name="input", required=True), SkillParameter(name="lang", default="en")],
)


class TestLoadSlashCommand:
    async def test_builds_a_load_skill_round(self):
        loaded = await load_slash_command(_service(_TRIAGE), parse_slash_command("/triage job_id=j-1"), actor="u1")

        assert (loaded.name, loaded.revision) == ("triage", 4)
        call_message, tool_message = loaded.messages
        [call] = call_message["tool_calls"]
        [entry] = tool_message["tool_results"]
        assert call_message["role"] == "assistant" and call_message["content"] == ""
        assert call == {
            "name": "load_skill",
            "args": {"name": "triage", "params": {"job_id": "j-1"}},
            "id": call["id"],
            "type": "tool_call",
        }
        assert call_message["metadata"]["message_id"] == loaded.message_id
        assert call_message["metadata"]["direct"] is True and "usage" not in call_message["metadata"]
        assert tool_message["role"] == "tool" and tool_message["metadata"]["direct"] is True
        assert entry["tool_call_id"] == call["id"] and entry["name"] == "load_skill"
        assert entry["metadata"] == {"skill_ref": {"name": "triage", "revision": "4"}, "direct": True}
        assert entry["result"].startswith("Skill: triage (revision 4)")
        assert '- job_id = "j-1"' in entry["result"]

    async def test_tool_call_id_is_bedrock_safe(self):
        loaded = await load_slash_command(_service(_TRIAGE), parse_slash_command("/triage job_id=j-1"))
        call_id = loaded.messages[0]["tool_calls"][0]["id"]

        assert len(call_id) <= 64 and call_id.replace("_", "").replace("-", "").isalnum()

    async def test_free_text_becomes_the_input_parameter(self):
        loaded = await load_slash_command(_service(_SUMMARIZE), parse_slash_command("/summarize lang=fr the build log"))

        assert loaded.messages[0]["tool_calls"][0]["args"]["params"] == {"lang": "fr", "input": "the build log"}
        assert '- input = "the build log"' in loaded.messages[1]["tool_results"][0]["result"]

    async def test_free_text_is_refused_without_an_input_parameter(self):
        with pytest.raises(SkillLoadError) as error:
            await load_slash_command(_service(_TRIAGE), parse_slash_command("/triage job_id=j-1 and more"))

        assert error.value.code == INVALID_SKILL_ARGUMENTS
        assert "free text" in error.value.message

    async def test_input_as_both_pair_and_free_text_is_refused(self):
        with pytest.raises(SkillLoadError) as error:
            await load_slash_command(_service(_SUMMARIZE), parse_slash_command("/summarize input=a b"))

        assert error.value.code == INVALID_SKILL_ARGUMENTS

    async def test_unknown_and_disabled_skills(self):
        disabled = _TRIAGE.model_copy(update={"enabled": False})

        with pytest.raises(SkillLoadError) as unknown:
            await load_slash_command(_service(_TRIAGE), parse_slash_command("/nope"))
        with pytest.raises(SkillLoadError) as off:
            await load_slash_command(_service(disabled), parse_slash_command("/triage job_id=1"))

        assert unknown.value.code == "unknown_skill"
        assert off.value.code == "skill_disabled"

    async def test_missing_required_parameter(self):
        with pytest.raises(SkillLoadError) as error:
            await load_slash_command(_service(_TRIAGE), parse_slash_command("/triage"))

        assert error.value.code == INVALID_SKILL_ARGUMENTS
        assert "job_id" in error.value.message
