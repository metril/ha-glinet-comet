"""Test fixtures: stub Home Assistant + voluptuous so the package imports.

The package ``__init__`` (and the api/coordinator/services chain it pulls in)
imports Home Assistant, which isn't installed in CI. The pure modules under test
(api, parsers) don't actually use HA, so we inject minimal stubs — mirroring
the approach used in the ha-glinet integration's test suite.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _mod(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _stub_voluptuous() -> None:
    if "voluptuous" in sys.modules:
        return
    vol = types.ModuleType("voluptuous")

    class _Schema:
        def __init__(self, schema, *a, **k):
            self._schema = schema

        def __call__(self, data):
            return data

    class _Marker:
        def __init__(self, key, *a, **k):
            self.key = key

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return self.key == getattr(other, "key", other)

    vol.Schema = _Schema
    vol.Required = _Marker
    vol.Optional = _Marker
    vol.All = lambda *a: (a[-1] if a else (lambda x: x))
    vol.Range = lambda *a, **k: (lambda x: x)
    vol.Coerce = lambda tp: tp
    vol.In = lambda *a, **k: (lambda x: x)
    sys.modules["voluptuous"] = vol


def _stub_homeassistant() -> None:
    if getattr(sys.modules.get("homeassistant"), "_glinet_comet_stub", False):
        return

    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    ha = _mod("homeassistant")
    ha._glinet_comet_stub = True

    class _SupportsResponse:
        NONE = "none"
        ONLY = "only"
        OPTIONAL = "optional"

    ha_core = _mod(
        "homeassistant.core",
        HomeAssistant=MagicMock,
        ServiceCall=MagicMock,
        ServiceResponse=dict,
        SupportsResponse=_SupportsResponse,
        callback=lambda f: f,
    )
    ha_ce = _mod(
        "homeassistant.config_entries",
        ConfigEntry=MagicMock,
        ConfigFlow=object,
        ConfigFlowResult=dict,
        OptionsFlow=object,
    )

    class _Platform:
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        CAMERA = "camera"
        DEVICE_TRACKER = "device_tracker"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"
        TEXT = "text"
        UPDATE = "update"

    ha_const = _mod("homeassistant.const", Platform=_Platform)
    ha_exc = _mod(
        "homeassistant.exceptions",
        HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
    )

    ha_helpers = _mod("homeassistant.helpers")

    class _DUC(Generic[_T]):
        def __init__(self, hass=None, logger=None, *, name="", update_interval=None, **k):
            self.data = None

        def __init_subclass__(cls, **k):
            super().__init_subclass__()

        def async_update_listeners(self) -> None:
            pass

        def async_set_updated_data(self, data) -> None:
            self.data = data

    class _CE(Generic[_T]):
        def __init__(self, coordinator=None, **k):
            self.coordinator = coordinator

        def __init_subclass__(cls, **k):
            super().__init_subclass__()

    ha_uc = _mod(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DUC,
        CoordinatorEntity=_CE,
        UpdateFailed=ha_exc.UpdateFailed,
    )
    ha_ac = _mod(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=MagicMock(return_value=MagicMock()),
    )
    ha_cv = _mod("homeassistant.helpers.config_validation", string=str, boolean=bool)
    ha_evt = _mod(
        "homeassistant.helpers.event",
        async_call_later=lambda hass, delay, action: (lambda: None),
    )
    ha_dr = _mod(
        "homeassistant.helpers.device_registry",
        DeviceInfo=dict,
        CONNECTION_NETWORK_MAC="mac",
        async_get=MagicMock(),
    )

    ha_helpers.update_coordinator = ha_uc
    ha_helpers.aiohttp_client = ha_ac
    ha_helpers.config_validation = ha_cv
    ha_helpers.device_registry = ha_dr
    ha_helpers.event = ha_evt

    modules = {
        "homeassistant": ha,
        "homeassistant.core": ha_core,
        "homeassistant.config_entries": ha_ce,
        "homeassistant.const": ha_const,
        "homeassistant.exceptions": ha_exc,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
        "homeassistant.helpers.aiohttp_client": ha_ac,
        "homeassistant.helpers.config_validation": ha_cv,
        "homeassistant.helpers.device_registry": ha_dr,
        "homeassistant.helpers.event": ha_evt,
    }
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


_stub_voluptuous()
_stub_homeassistant()
