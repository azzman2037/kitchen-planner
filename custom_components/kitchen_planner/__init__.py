"""Kitchen Planner: turn the Mealie dinner plan into a Coles-ready order."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_REFRESH
from .coordinator import KitchenPlannerCoordinator

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR, Platform.TODO]

type KitchenPlannerConfigEntry = ConfigEntry[KitchenPlannerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: KitchenPlannerConfigEntry) -> bool:
    """Set up Kitchen Planner from a config entry."""
    coordinator = KitchenPlannerCoordinator(hass, entry)
    # Do not fail setup if the data folder or Mealie is not ready yet: the
    # entities show unavailable and the refresh button retries.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async def _refresh(call: ServiceCall) -> None:
        for e in hass.config_entries.async_loaded_entries(DOMAIN):
            await e.runtime_data.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    return True


async def _async_reload(hass: HomeAssistant, entry: KitchenPlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: KitchenPlannerConfigEntry) -> bool:
    """Unload a Kitchen Planner entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
