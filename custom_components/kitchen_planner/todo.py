"""The Coles order as a to-do list the whole family can tick and add to.

Generated lines come from the planner and are rebuilt on every refresh.
Ticks and removals are kept in storage per week, so switching the dashboard
between weeks keeps each week's list as it was left. Hand-added items belong
to no week: they stay until ticked.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import KitchenPlannerCoordinator
from .entity import KitchenPlannerEntity
from .planner import order_items

STORAGE_VERSION = 1
KEEP_WEEKS = 6


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    entity = ColesOrderList(entry.runtime_data, hass, entry.entry_id)
    await entity.async_load()
    async_add_entities([entity])


class ColesOrderList(KitchenPlannerEntity, TodoListEntity):
    _attr_icon = "mdi:cart-variant"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: KitchenPlannerCoordinator, hass: HomeAssistant,
                 entry_id: str) -> None:
        super().__init__(coordinator, "coles_order")
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.coles_order")
        self._state: dict[str, Any] = {"weeks": {}, "manual": []}

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        if "weeks" not in stored:  # 0.3.x kept one week at the top level
            week = stored.get("week")
            stored = {"weeks": ({week: {"done": stored.get("done", []),
                                        "removed": stored.get("removed", [])}} if week else {}),
                      "manual": stored.get("manual", [])}
        self._state = stored

    async def _save(self) -> None:
        await self._store.async_save(self._state)

    def _week(self) -> dict[str, list[str]]:
        """Ticks and removals for the week on screen."""
        week = (self.coordinator.data or {}).get("week_start") or "none"
        return self._state["weeks"].setdefault(week, {"done": [], "removed": []})

    def _prune(self) -> None:
        weeks = self._state["weeks"]
        for old in sorted(weeks)[:-KEEP_WEEKS]:
            del weeks[old]
        # A ticked hand-added item drops off a week after it was ticked.
        cutoff = (dt_util.now().date() - timedelta(days=7)).isoformat()
        self._state["manual"] = [m for m in self._state["manual"]
                                 if not (m.get("done_on") and m["done_on"] < cutoff)]

    @property
    def todo_items(self) -> list[TodoItem]:
        if self.coordinator.data is None:
            return []
        wk = self._week()
        done, removed = set(wk["done"]), set(wk["removed"])
        items = [
            TodoItem(
                uid=line["uid"], summary=line["summary"],
                description=line["description"],
                status=(TodoItemStatus.COMPLETED
                        if line["uid"] in done or line.get("ordered")
                        else TodoItemStatus.NEEDS_ACTION),
            )
            for line in order_items(self.coordinator.data)
            if line["uid"] not in removed
        ]
        items += [
            TodoItem(uid=m["uid"], summary=m["summary"],
                     description=m.get("description"),
                     status=TodoItemStatus(m["status"]))
            for m in self._state["manual"]
        ]
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self._state["manual"].append({
            "uid": "manual:" + uuid4().hex, "summary": item.summary,
            "description": item.description,
            "status": (item.status or TodoItemStatus.NEEDS_ACTION).value,
        })
        await self._save()
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        uid = item.uid or ""
        if uid.startswith("manual:"):
            for m in self._state["manual"]:
                if m["uid"] == uid:
                    m["summary"] = item.summary or m["summary"]
                    m["description"] = item.description
                    m["status"] = (item.status or TodoItemStatus.NEEDS_ACTION).value
                    if m["status"] == TodoItemStatus.COMPLETED.value:
                        m.setdefault("done_on", dt_util.now().date().isoformat())
                    else:
                        m.pop("done_on", None)
        else:
            wk = self._week()
            done = set(wk["done"])
            if item.status == TodoItemStatus.COMPLETED:
                done.add(uid)
            else:
                done.discard(uid)
            wk["done"] = sorted(done)
        await self._save()
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        manual = [u for u in uids if u.startswith("manual:")]
        self._state["manual"] = [m for m in self._state["manual"]
                                 if m["uid"] not in manual]
        wk = self._week()
        wk["removed"] = sorted(set(wk["removed"]) | {u for u in uids if u not in manual})
        await self._save()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._prune()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._prune()
        super()._handle_coordinator_update()
