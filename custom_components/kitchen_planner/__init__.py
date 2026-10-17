"""Kitchen Planner: turn the Mealie dinner plan into a Coles-ready order."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SERVICE_CLEAR_ON_HAND,
    SERVICE_IMPORT_FILE,
    SERVICE_REFRESH,
    SERVICE_SET_ON_HAND,
)
from .coordinator import KitchenPlannerCoordinator
from .planner import import_file

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.TODO]

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

    async def _import(call: ServiceCall) -> ServiceResponse:
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            raise ServiceValidationError("Kitchen Planner is not set up")
        coord: KitchenPlannerCoordinator = loaded[0].runtime_data
        stamp = dt_util.now().strftime("%Y%m%d-%H%M%S")
        try:
            result = await hass.async_add_executor_job(
                import_file, coord.data_dir, call.data["filename"],
                call.data["data"], stamp)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        await coord.async_request_refresh()
        return result

    def _coord() -> KitchenPlannerCoordinator:
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            raise ServiceValidationError("Kitchen Planner is not set up")
        return loaded[0].runtime_data

    async def _set_on_hand(call: ServiceCall) -> None:
        d = call.data
        if ("qty" in d) != ("unit" in d):
            raise ServiceValidationError("Give qty and unit together, or packs")
        await _coord().async_set_on_hand(
            d["canonical"], packs=d.get("packs"), qty=d.get("qty"),
            unit=d.get("unit"), until=d.get("until"))

    async def _clear_on_hand(call: ServiceCall) -> None:
        await _coord().async_clear_on_hand(call.data["canonical"])

    if not hass.services.has_service(DOMAIN, SERVICE_SET_ON_HAND):
        hass.services.async_register(DOMAIN, SERVICE_SET_ON_HAND, _set_on_hand, schema=vol.Schema({
            vol.Required("canonical"): cv.string,
            vol.Optional("packs"): vol.Coerce(float),
            vol.Optional("qty"): vol.Coerce(float),
            vol.Optional("unit"): vol.In(["g", "ml", "each"]),
            vol.Optional("until"): cv.date,
        }))
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_ON_HAND, _clear_on_hand,
                                     schema=vol.Schema({vol.Required("canonical"): cv.string}))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
        hass.services.async_register(
            DOMAIN, SERVICE_IMPORT_FILE, _import,
            schema=vol.Schema({vol.Required("filename"): str, vol.Required("data"): str}),
            supports_response=SupportsResponse.OPTIONAL)
    return True


async def _async_reload(hass: HomeAssistant, entry: KitchenPlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: KitchenPlannerConfigEntry) -> bool:
    """Unload a Kitchen Planner entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
