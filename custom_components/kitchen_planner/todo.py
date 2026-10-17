"""The Coles order as a to-do list the whole family can tick and add to.

Generated lines come from the planner and are rebuilt on every refresh.
Ticks, removals and hand-added items are kept in storage, keyed to the
planning week, so they survive the rebuild and reset when the week rolls on.
"""
from __future__ import annotations

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

from .const import DOMAIN
from .coordinator import KitchenPlannerCoordinator
from .entity import KitchenPlannerEntity
from .planner import order_items

STORAGE_VERSION = 1


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
        self._state: dict[str, Any] = self._blank(None)

    @staticmethod
    def _blank(week: str | None) -> dict[str, Any]:
        return {"week": week, "done": [], "removed": [], "manual": []}

    async def async_load(self) -> None:
        self._state = await self._store.async_load() or self._blank(None)

    async def _save(self) -> None:
        await self._store.async_save(self._state)

    def _roll_week(self) -> None:
        week = (self.coordinator.data or {}).get("week_start")
        if week and self._state.get("week") != week:
            # Hand-added items carry over until they are ticked; the rest resets.
            manual = [m for m in self._state["manual"] if m["status"] != "completed"]
            self._state = {**self._blank(week), "manual": manual}
            self.hass.async_create_task(self._save())

    @property
    def todo_items(self) -> list[TodoItem]:
        if self.coordinator.data is None:
            return []
        done, removed = set(self._state["done"]), set(self._state["removed"])
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
        else:
            done = set(self._state["done"])
            if item.status == TodoItemStatus.COMPLETED:
                done.add(uid)
            else:
                done.discard(uid)
            self._state["done"] = sorted(done)
        await self._save()
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        manual = [u for u in uids if u.startswith("manual:")]
        self._state["manual"] = [m for m in self._state["manual"]
                                 if m["uid"] not in manual]
        removed = set(self._state["removed"]) | {u for u in uids if u not in manual}
        self._state["removed"] = sorted(removed)
        await self._save()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._roll_week()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._roll_week()
        super()._handle_coordinator_update()
