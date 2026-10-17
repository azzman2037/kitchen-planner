"""End-to-end tests inside a test Home Assistant with a fake Mealie."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kitchen_planner.const import (
    CONF_DATA_DIR,
    CONF_HORIZON,
    CONF_MEALIE_ENTRY,
    CONF_SERVES,
    DOMAIN,
)

FIXTURE = Path(__file__).parent / "fixtures" / "data"


def _plan() -> list[dict]:
    return [
        {"entry_type": "dinner", "mealplan_date": date(2026, 10, 19), "title": "",
         "recipe": {"slug": "butter-chicken", "name": "Butter Chicken"}},
        {"entry_type": "dinner", "mealplan_date": date(2026, 10, 20), "title": "",
         "recipe": {"slug": "creamy-tuscan-garlic-chicken", "name": "Tuscan Chicken"}},
    ]


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path,
                 with_data: bool = True) -> MockConfigEntry:
    freezer.move_to("2026-10-17 09:00:00+11:00")
    await hass.config.async_set_time_zone("Australia/Sydney")
    if with_data:
        await hass.async_add_executor_job(shutil.copytree, FIXTURE, tmp_path / "kp")
    mealie = MockConfigEntry(domain="mealie", state=ConfigEntryState.LOADED)
    mealie.add_to_hass(hass)
    hass.data["mealie_calls"] = []

    async def _get_mealplan(call: ServiceCall) -> dict:
        hass.data["mealie_calls"].append(dict(call.data))
        return {"mealplan": _plan()}

    hass.services.async_register("mealie", "get_mealplan", _get_mealplan,
                                 supports_response=SupportsResponse.ONLY)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={
        CONF_MEALIE_ENTRY: mealie.entry_id, CONF_SERVES: 4, CONF_HORIZON: 10,
        CONF_DATA_DIR: str(tmp_path / "kp")})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant) -> None:
    mealie = MockConfigEntry(domain="mealie", state=ConfigEntryState.LOADED)
    mealie.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    ok = await hass.config_entries.flow.async_configure(result["flow_id"], {
        CONF_MEALIE_ENTRY: mealie.entry_id, CONF_SERVES: 4.0, CONF_HORIZON: 10.0,
        CONF_DATA_DIR: " /kitchen_planner/ "})
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    assert ok["data"] == {CONF_MEALIE_ENTRY: mealie.entry_id, CONF_SERVES: 4,
                          CONF_HORIZON: 10, CONF_DATA_DIR: "kitchen_planner"}
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_entities(hass: HomeAssistant, freezer: FrozenDateTimeFactory,
                        tmp_path: Path) -> None:
    await _setup(hass, freezer, tmp_path)
    call = hass.data["mealie_calls"][0]
    assert (call["start_date"], call["end_date"]) == ("2026-10-19", "2026-10-25")

    dinners = hass.states.get("sensor.kitchen_planner_dinners_planned")
    assert dinners.state == "2"
    assert [d["name"] for d in dinners.attributes["days"]] == ["Butter Chicken", "Tuscan Chicken"]

    total = hass.states.get("sensor.kitchen_planner_estimated_total")
    assert float(total.state) > 4.65
    assert total.attributes["unit_of_measurement"] == "AUD"
    assert hass.states.get("sensor.kitchen_planner_staples_due").state == "1"
    left = hass.states.get("sensor.kitchen_planner_leftovers")
    assert "cream_thickened" in {i["canonical"] for i in left.attributes["items"]}

    items = await hass.services.async_call(
        "todo", "get_items", {"entity_id": "todo.kitchen_planner_coles_order"},
        blocking=True, return_response=True)
    summaries = [i["summary"] for i in items["todo.kitchen_planner_coles_order"]["items"]]
    assert any("cream" in s.lower() for s in summaries)
    assert any("Milk" in s for s in summaries)


async def test_todo_ticks_and_manual_items_survive_refresh(
        hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path) -> None:
    await _setup(hass, freezer, tmp_path)
    eid = "todo.kitchen_planner_coles_order"

    async def _items() -> list[dict]:
        r = await hass.services.async_call("todo", "get_items", {"entity_id": eid},
                                           blocking=True, return_response=True)
        return r[eid]["items"]

    cream = next(i for i in await _items() if "cream" in i["summary"].lower())
    await hass.services.async_call("todo", "update_item",
                                   {"entity_id": eid, "item": cream["uid"], "status": "completed"},
                                   blocking=True)
    await hass.services.async_call("todo", "add_item",
                                   {"entity_id": eid, "item": "Birthday candles"}, blocking=True)
    milk = next(i for i in await _items() if "Milk" in i["summary"])
    await hass.services.async_call("todo", "remove_item",
                                   {"entity_id": eid, "item": milk["uid"]}, blocking=True)

    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.kitchen_planner_refresh"}, blocking=True)
    await hass.async_block_till_done()

    after = {i["summary"]: i for i in await _items()}
    assert after[cream["summary"]]["status"] == "completed"
    assert after["Birthday candles"]["status"] == "needs_action"
    assert milk["summary"] not in after
    assert len(hass.data["mealie_calls"]) >= 2


async def test_missing_data_leaves_entities_unavailable(
        hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path) -> None:
    entry = await _setup(hass, freezer, tmp_path, with_data=False)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.kitchen_planner_dinners_planned").state == "unavailable"
    assert hass.states.get("button.kitchen_planner_refresh").state != "unavailable"


async def test_import_file_writes_backs_up_and_refreshes(
        hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path) -> None:
    import base64
    import gzip
    import json as _json

    import pytest
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass, freezer, tmp_path)
    folder = tmp_path / "kp"
    before = (folder / "pantry-state.json").read_text()
    new = {"cream_thickened": {"on_hand": {"qty": 300, "unit": "ml"}}}
    payload = base64.b64encode(gzip.compress(_json.dumps(new).encode())).decode()

    resp = await hass.services.async_call(
        DOMAIN, "import_file", {"filename": "pantry-state.json", "data": payload},
        blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert resp["entries"] == 1
    assert _json.loads((folder / "pantry-state.json").read_text()) == new
    assert (folder / resp["backup"]).read_text() == before

    # 300 mL now on hand covers the week's 350 mL down to 50 mL.
    left = hass.states.get("sensor.kitchen_planner_leftovers")
    assert left is not None

    with pytest.raises(ServiceValidationError, match="not a planner data file"):
        await hass.services.async_call(
            DOMAIN, "import_file", {"filename": "../secrets.yaml", "data": payload},
            blocking=True, return_response=True)
    with pytest.raises(ServiceValidationError, match="gzip"):
        await hass.services.async_call(
            DOMAIN, "import_file", {"filename": "recipes.json", "data": "not-base64!"},
            blocking=True, return_response=True)
    assert (folder / "recipes.json").exists()
