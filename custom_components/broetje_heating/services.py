"""Debug services for raw Modbus register access.

DANGER: These services bypass the register_map entirely. They read and
write raw register words directly by address, with no knowledge of what
a register actually means, no bounds checking, and no scaling. They are
intended for diagnosing undocumented registers and are only usable for
config entries that have explicitly enabled "Debug register access" in
the integration options.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_ADDRESS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_CONFIRM,
    ATTR_COUNT,
    ATTR_DATA_TYPE,
    ATTR_REGISTER_TYPE,
    ATTR_VALUES,
    CONF_DEBUG_REGISTER_ACCESS,
    DOMAIN,
    REG_HOLDING,
    REG_INPUT,
    SERVICE_READ_REGISTER,
    SERVICE_WRITE_REGISTER,
)
from .coordinator import BroetjeModbusCoordinator

MAX_MODBUS_REGISTERS = 125

SERVICE_READ_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_ADDRESS): vol.All(int, vol.Range(min=0, max=65535)),
        vol.Optional(ATTR_COUNT, default=1): vol.All(
            int, vol.Range(min=1, max=MAX_MODBUS_REGISTERS)
        ),
        vol.Optional(ATTR_REGISTER_TYPE, default=REG_HOLDING): vol.In(
            [REG_HOLDING, REG_INPUT]
        ),
        vol.Optional(ATTR_DATA_TYPE, default="raw"): vol.In(
            ["raw", "uint16", "int16", "uint32", "int32", "string"]
        ),
    }
)

SERVICE_WRITE_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_ADDRESS): vol.All(int, vol.Range(min=0, max=65535)),
        vol.Required(ATTR_VALUES): vol.All(
            cv.ensure_list,
            [vol.All(int, vol.Range(min=0, max=65535))],
            vol.Length(min=1, max=MAX_MODBUS_REGISTERS),
        ),
        vol.Required(ATTR_CONFIRM): vol.Equal(
            True,
            msg="You must set confirm: true to acknowledge the risk of a raw write",
        ),
    }
)


def _decode_registers(registers: list[int], data_type: str) -> Any:
    """Decode raw register words for display purposes only."""
    if data_type == "raw":
        return registers
    if data_type == "uint16":
        return registers[0]
    if data_type == "int16":
        value = registers[0]
        return value - 65536 if value >= 32768 else value
    if data_type in ("uint32", "int32"):
        if len(registers) < 2:
            raise ServiceValidationError(
                f"data_type '{data_type}' requires count=2, got {len(registers)}"
            )
        value = (registers[0] << 16) | registers[1]
        if data_type == "int32" and value >= 2147483648:
            value -= 4294967296
        return value
    if data_type == "string":
        chars = [chr((reg >> 8) & 0xFF) + chr(reg & 0xFF) for reg in registers]
        return "".join(chars).rstrip("\x00").strip()
    return registers


def _get_debug_coordinator(
    hass: HomeAssistant, config_entry_id: str
) -> BroetjeModbusCoordinator:
    """Resolve and authorize the coordinator for a debug service call."""
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            f"'{config_entry_id}' is not a Brötje Heating config entry"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(f"Config entry '{entry.title}' is not loaded")
    if not entry.options.get(CONF_DEBUG_REGISTER_ACCESS, False):
        raise ServiceValidationError(
            f"Debug register access is disabled for '{entry.title}'. Enable "
            "'Debug register access (DANGER)' in the integration options "
            "before using this action."
        )
    return entry.runtime_data


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the debug register read/write services (once per HA instance)."""
    if hass.services.has_service(DOMAIN, SERVICE_READ_REGISTER):
        return

    async def _async_read_register(call: ServiceCall) -> dict[str, Any]:
        coordinator = _get_debug_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        address = call.data[ATTR_ADDRESS]
        count = call.data[ATTR_COUNT]
        register_type = call.data[ATTR_REGISTER_TYPE]
        data_type = call.data[ATTR_DATA_TYPE]

        registers = await coordinator.async_debug_read_registers(
            address, count, register_type
        )

        return {
            "address": address,
            "count": count,
            "register_type": register_type,
            "data_type": data_type,
            "raw_registers": registers,
            "hex": [f"0x{value:04X}" for value in registers],
            "decoded": _decode_registers(registers, data_type),
        }

    async def _async_write_register(call: ServiceCall) -> dict[str, Any]:
        coordinator = _get_debug_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        address = call.data[ATTR_ADDRESS]
        values = call.data[ATTR_VALUES]

        readback = await coordinator.async_debug_write_registers(address, values)

        return {
            "address": address,
            "values_written": values,
            "readback": readback,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_REGISTER,
        _async_read_register,
        schema=SERVICE_READ_REGISTER_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_REGISTER,
        _async_write_register,
        schema=SERVICE_WRITE_REGISTER_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
