"""Default skills seeded from preset prompts (XMGPLAT-12316)."""

import asyncio
import logging

import pytest

from ._autolangchat_imports import clear_stale_stub_modules

clear_stale_stub_modules()

from autolangchat.db import SQLiteSkillStore  # noqa: E402
from autolangchat.exceptions import SkillAlreadyExistsError  # noqa: E402
from autolangchat.skills import Skill  # noqa: E402
from autolangchat.skills.prompt import LOAD_SKILL  # noqa: E402
from autolangchat.skills.seeding import PRESET_SEED_ACTOR, preset_to_skill, seed_preset_skills  # noqa: E402

from .test_skill_store import _make_postgres_store  # noqa: E402

LIMITS = dict(instructions_max_chars=32000, description_max_chars=300, max_parameters=20)

VARIABLES = [
    {
        "name": "JOB_ID",
        "label": "Job ID",
        "input_type": "text",
        "validate": "^[0-9a-f-]+$",
        "placeholder": "e.g. e62f2481-...",
        "detect_pattern": "[0-9a-f]{8}",
        "default": "abc",
    },
    {"name": "NEW_JOB_ID", "label": "New Job ID", "placeholder": "e.g. 2481e62f-..."},
    {"name": "MODE", "label": "Mode", "input_type": "select", "options": ["fast", 2]},
]


def _preset(id_="workload-analysis-iwps", label="Workload Analysis - IWPS", **overrides):
    preset = {
        "id": id_,
        "label": label,
        "group": "Workload Analysis",
        "description": "Full CPU workload report\n(IPC, memory)\n",
        "template": "JOB_ID = {{JOB_ID}}\nAnalyse {{JOB_ID}} now.\n",
    }
    preset.update(overrides)
    return preset


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_preset_maps_to_skill_fields():
    skill = preset_to_skill(_preset(), VARIABLES)

    assert skill.name == "workload-analysis-iwps"
    assert skill.display_name == "Workload Analysis - IWPS"
    assert skill.description == "Full CPU workload report\n(IPC, memory)"
    assert skill.instructions == "JOB_ID = {{job_id}}\nAnalyse {{job_id}} now.\n"
    assert skill.enabled is True


def test_placeholders_become_required_lowercase_parameters_in_template_order():
    skill = preset_to_skill(
        _preset(template="Base {{JOB_ID}} vs {{NEW_JOB_ID}}, again {{JOB_ID}}, mode {{MODE}}"), VARIABLES
    )

    assert [p.name for p in skill.parameters] == ["job_id", "new_job_id", "mode"]
    assert all(p.required for p in skill.parameters)
    assert skill.instructions == "Base {{job_id}} vs {{new_job_id}}, again {{job_id}}, mode {{mode}}"


def test_variable_hints_are_copied_and_other_fields_dropped():
    job_id, mode = preset_to_skill(_preset(template="{{JOB_ID}} {{MODE}}"), VARIABLES).parameters

    assert job_id.model_dump() == {
        "name": "job_id",
        "label": "Job ID",
        "help": "",
        "required": True,
        "default": None,  # preset defaults are not carried over
        "input_type": "text",
        "options": [],
        "placeholder": "e.g. e62f2481-...",
        "example": "",
    }
    assert (mode.input_type, mode.options) == ("select", ["fast", "2"])


def test_only_placeholders_used_by_the_template_become_parameters():
    skill = preset_to_skill(_preset(template="Summarise API health."), VARIABLES)

    assert skill.parameters == []


def test_undeclared_placeholder_gets_a_bare_parameter():
    (parameter,) = preset_to_skill(_preset(template="{{TENANT}}"), VARIABLES).parameters

    assert (parameter.name, parameter.label, parameter.required) == ("tenant", "", True)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(label="x" * 101),
        dict(template="   "),
        dict(id_="-bad"),
        dict(template="{{_LEADING}}"),
    ],
)
def test_invalid_preset_raises_value_error(overrides):
    with pytest.raises(ValueError):
        preset_to_skill(_preset(**overrides), VARIABLES)


# ---------------------------------------------------------------------------
# Seeding (both backends)
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "sqlite":
        store = SQLiteSkillStore(str(tmp_path / "skills.db"))
        await store.open()
        yield store
        await store.close()
    else:
        yield _make_postgres_store()


PRESETS = [
    _preset(),
    _preset("regression-check", "Regression Check", template="{{JOB_ID}} vs {{NEW_JOB_ID}}"),
]


async def _seed(store, presets=PRESETS, **limits):
    return await seed_preset_skills(store, presets, VARIABLES, **{**LIMITS, **limits})


async def test_fresh_seed_creates_every_preset_as_an_enabled_skill(store):
    assert await _seed(store) == ["workload-analysis-iwps", "regression-check"]

    skills = {skill.name: skill for skill in await store.list_skills()}
    assert set(skills) == {"workload-analysis-iwps", "regression-check"}
    assert all(skill.enabled and skill.updated_by == PRESET_SEED_ACTOR for skill in skills.values())
    assert [p.name for p in skills["regression-check"].parameters] == ["job_id", "new_job_id"]


async def test_restart_is_idempotent(store):
    await _seed(store)
    before = await store.list_skills()

    assert await _seed(store) == []

    assert await store.list_skills() == before


async def test_admin_edits_and_disables_survive_a_restart(store):
    await _seed(store)
    await store.replace_skill(
        Skill(name="workload-analysis-iwps", display_name="Edited", instructions="edited"), updated_by="admin"
    )
    await store.set_enabled("regression-check", False, updated_by="admin")

    await _seed(store)

    edited = await store.get_skill("workload-analysis-iwps")
    assert (edited.instructions, edited.updated_by) == ("edited", "admin")
    assert (await store.get_skill("regression-check")).enabled is False


async def test_deleted_default_is_recreated(store):
    await _seed(store)
    await store.delete_skill("regression-check")

    assert await _seed(store) == ["regression-check"]

    assert (await store.get_skill("regression-check")).enabled is True


async def test_renamed_label_creates_a_skill_under_the_new_slug(store):
    await _seed(store)

    renamed = [_preset("workload-analysis-iwps-v2", "Workload Analysis - IWPS v2")]
    assert await _seed(store, renamed) == ["workload-analysis-iwps-v2"]

    assert await store.get_skill("workload-analysis-iwps") is not None


async def test_invalid_preset_is_logged_and_skipped(store, caplog):
    presets = [_preset("bad", "Bad", template="x" * 11), PRESETS[1]]

    with caplog.at_level(logging.WARNING, logger="autolangchat.skills.seeding"):
        created = await _seed(store, presets, instructions_max_chars=10)

    assert created == []  # regression-check is too long as well
    assert await store.list_skills() == []
    assert sum("Skipping default skill" in r.message for r in caplog.records) == 2

    assert await _seed(store, [_preset("-bad"), PRESETS[1]]) == ["regression-check"]


async def test_concurrent_starts_seed_each_skill_once(store):
    first, second = await asyncio.gather(_seed(store), _seed(store))

    assert sorted(first + second) == ["regression-check", "workload-analysis-iwps"]
    assert len(await store.list_skills()) == 2


async def test_conflict_with_another_process_counts_as_seeded():
    class _LostRace:
        async def create_skill(self, skill, updated_by=None):
            raise SkillAlreadyExistsError(skill.name)

    assert await _seed(_LostRace()) == []


async def test_other_store_failures_propagate():
    class _Down:
        async def create_skill(self, skill, updated_by=None):
            raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await _seed(_Down())


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


def _plugin(store, presets, variables=VARIABLES):
    from fastapi import FastAPI

    from autolangchat.config import ChatConfig
    from autolangchat.plugin import AutoLangChatPlugin

    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = FastAPI()
    plugin.config = ChatConfig(model_id="test-model", AUTOCHAT_ENABLE_UI=False, AUTOCHAT_ARTIFACT_ENABLED=False)
    plugin._preset_prompts = presets
    plugin._preset_variables = variables
    plugin._assign_preset_prompt_ids()
    plugin._skill_store = store
    plugin._init_tools()
    return plugin


def test_preset_ids_are_label_slugs_and_explicit_ids_are_ignored(caplog):
    presets = [
        {"id": "custom", "label": "Workload Analysis - IWPS", "template": "t"},
        {"label": "Workload Analysis - IWPS", "template": "t"},
        {"label": "Regression Check", "template": "t"},
    ]

    with caplog.at_level(logging.WARNING, logger="autolangchat.plugin"):
        _plugin(None, presets)

    assert [p["id"] for p in presets] == ["workload-analysis-iwps", "workload-analysis-iwps-2", "regression-check"]
    assert any("Ignoring explicit id 'custom'" in r.message for r in caplog.records)


async def test_startup_seeds_skills_into_the_catalog_and_binds_load_skill(tmp_path):
    store = SQLiteSkillStore(str(tmp_path / "skills.db"))
    presets = [
        {"label": "Workload Analysis - IWPS", "description": "IWPS report", "template": "{{JOB_ID}}"},
        {"label": "Regression Check", "description": "Compare", "template": "{{JOB_ID}} {{NEW_JOB_ID}}"},
    ]
    plugin = _plugin(store, presets)

    await plugin._startup_open_skill_store()
    await plugin._startup_seed_preset_skills()
    await plugin.tool_manager.refresh()  # what the first chat turn does

    try:
        entries = plugin.tool_manager.skill_snapshot.entries
        assert [e.name for e in entries] == ["regression-check", "workload-analysis-iwps"]
        assert LOAD_SKILL in {t.name for t in plugin.config.langchain_tools}
    finally:
        await store.close()


async def test_seeding_failure_does_not_stop_startup(caplog):
    class _Down:
        async def create_skill(self, skill, updated_by=None):
            raise RuntimeError("db down")

    plugin = _plugin(_Down(), [{"label": "A", "template": "t"}])
    version = plugin._skill_service.version

    with caplog.at_level(logging.ERROR, logger="autolangchat.plugin"):
        await plugin._startup_seed_preset_skills()

    assert any("Failed to seed default skills" in r.message for r in caplog.records)
    assert plugin._skill_service.version == version + 1
