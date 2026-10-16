"""Base entity for Kitchen Planner."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KitchenPlannerCoordinator


class KitchenPlannerEntity(CoordinatorEntity[KitchenPlannerCoordinator]):
    """All Kitchen Planner entities hang off one service device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KitchenPlannerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Kitchen Planner",
            manufacturer="azzman2037",
            entry_type=DeviceEntryType.SERVICE,
        )
