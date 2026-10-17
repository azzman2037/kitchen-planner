"""Sensors: the week's dinners, the estimated spend, staples due and leftovers."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import KitchenPlannerCoordinator
from .entity import KitchenPlannerEntity
from .planner import order_items


@dataclass(frozen=True, kw_only=True)
class KPSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]
    attrs: Callable[[dict[str, Any]], dict[str, Any]]


def _dinners(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "week": d.get("week"),
        "week_start": d["week_start"],
        "days": [{k: day[k] for k in ("date", "day", "name", "slug", "recipe_id")}
                 for day in d["days"]],
        "unmapped": d["unmapped"],
    }


def _total(d: dict[str, Any]) -> dict[str, Any]:
    placed = d.get("placed")
    return {
        "ordered": bool(placed),
        "order": ({k: placed[k] for k in ("date", "number", "total")}
                  | {"lines": len(placed["items"])}) if placed else None,
        "is_floor": d["is_floor"],
        "recipe_cost": d["recipe_cost"],
        "staples_cost": d["staples_cost"],
        "lines": len(d["rows"]) + len(d["staples"]),
        "unpriced": d["unpriced"],
        "no_pack_size": d["no_pack_size"],
        "problems": d["problems"],
        "aisles": d["aisles"],
        "line_aisles": {i["uid"]: i["aisle"] for i in order_items(d)},
    }


def _staples(d: dict[str, Any]) -> dict[str, Any]:
    keep = ("canonical", "label", "typical_qty", "est_cost", "due_on",
            "median_interval_days", "last_purchased")
    return {
        "items": [{k: s.get(k) for k in keep} for s in d["staples"]],
        "to_check": [{k: s.get(k) for k in keep} for s in d["staples_to_check"]],
        "covered_by_recipes": d["absorbed"],
    }


SENSORS: tuple[KPSensorDescription, ...] = (
    KPSensorDescription(
        key="dinners_planned", icon="mdi:silverware-fork-knife",
        value=lambda d: sum(1 for day in d["days"] if not day.get("flex")),
        attrs=_dinners,
    ),
    KPSensorDescription(
        key="estimated_total", icon="mdi:cart-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD", suggested_display_precision=2,
        value=lambda d: d["estimated_total"], attrs=_total,
    ),
    KPSensorDescription(
        key="staples_due", icon="mdi:basket-fill",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda d: len(d["staples"]), attrs=_staples,
    ),
    KPSensorDescription(
        key="in_the_house", icon="mdi:fridge",
        value=lambda d: len(d["pantry"]["have"]),
        attrs=lambda d: dict(d["pantry"]),
    ),
    KPSensorDescription(
        key="weekly_spend", icon="mdi:chart-line",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD", suggested_display_precision=2,
        value=lambda d: d["spend"][-1]["total"] if d["spend"] else None,
        attrs=lambda d: {
            "history": d["spend"],
            "last_order": d["spend"][-1]["date"] if d["spend"] else None,
            "average_last_8": (round(sum(x["total"] for x in d["spend"][-8:])
                                     / len(d["spend"][-8:]), 2) if d["spend"] else None),
        },
    ),
    KPSensorDescription(
        key="leftovers", icon="mdi:fridge-alert-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda d: len(d["leftovers"]),
        attrs=lambda d: {"items": d["leftovers"]},
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator: KitchenPlannerCoordinator = entry.runtime_data
    async_add_entities(KPSensor(coordinator, desc) for desc in SENSORS)


class KPSensor(KitchenPlannerEntity, SensorEntity):
    entity_description: KPSensorDescription

    def __init__(self, coordinator: KitchenPlannerCoordinator,
                 desc: KPSensorDescription) -> None:
        super().__init__(coordinator, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs(self.coordinator.data)
