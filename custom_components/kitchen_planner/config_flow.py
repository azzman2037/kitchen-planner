"""Config flow for Kitchen Planner."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_DATA_DIR,
    CONF_HORIZON,
    CONF_MEALIE_ENTRY,
    CONF_SERVES,
    DEFAULT_DATA_DIR,
    DEFAULT_HORIZON,
    DEFAULT_SERVES,
    DOMAIN,
)


def _schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_MEALIE_ENTRY, default=d.get(CONF_MEALIE_ENTRY, vol.UNDEFINED)):
            ConfigEntrySelector(ConfigEntrySelectorConfig(integration="mealie")),
        vol.Required(CONF_SERVES, default=d.get(CONF_SERVES, DEFAULT_SERVES)):
            NumberSelector(NumberSelectorConfig(min=1, max=12, step=1,
                                                mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_HORIZON, default=d.get(CONF_HORIZON, DEFAULT_HORIZON)):
            NumberSelector(NumberSelectorConfig(min=1, max=28, step=1,
                                                mode=NumberSelectorMode.BOX,
                                                unit_of_measurement="days")),
        vol.Required(CONF_DATA_DIR, default=d.get(CONF_DATA_DIR, DEFAULT_DATA_DIR)):
            TextSelector(),
    })


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {**user_input,
            CONF_SERVES: int(user_input[CONF_SERVES]),
            CONF_HORIZON: int(user_input[CONF_HORIZON]),
            CONF_DATA_DIR: user_input[CONF_DATA_DIR].strip().strip("/")}


class KitchenPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Link the planner to Mealie and the household data folder."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Kitchen Planner", data=_clean(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> KitchenPlannerOptionsFlow:
        return KitchenPlannerOptionsFlow()


class KitchenPlannerOptionsFlow(OptionsFlowWithReload):
    """Change servings, horizon, Mealie link or data folder."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
