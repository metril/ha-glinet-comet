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
    _UNSET = object()

    class _AbortFlow(Exception):
        """Stand-in for homeassistant.data_entry_flow.AbortFlow."""

        def __init__(self, reason: str, description_placeholders=None) -> None:
            self.reason = reason
            self.description_placeholders = description_placeholders
            super().__init__(reason)

    class _UnknownEntry(RuntimeError):
        """Stand-in for homeassistant.config_entries.UnknownEntry.

        Raised by `_abort_if_unique_id_mismatch` below when neither
        `_reconfigure_entry` nor `_reauth_entry` is set, instead of
        silently returning -- so a future test that forgets to set one of
        those entries can't pass vacuously.
        """

    ha_daf = _mod(
        "homeassistant.data_entry_flow", AbortFlow=_AbortFlow, UnknownEntry=_UnknownEntry
    )

    class _ConfigFlow:
        """Minimal stand-in for homeassistant.config_entries.ConfigFlow."""

        def __init_subclass__(cls, domain=None, **k) -> None:
            super().__init_subclass__(**k)
            cls.domain = domain

        def __init__(self, *a, **k) -> None:
            self.hass = None
            self.context: dict = {}
            self.unique_id = None
            self._configured_unique_ids: set = set()
            self._reauth_entry = None
            self._reconfigure_entry = None

        async def async_set_unique_id(self, unique_id, raise_on_progress=True):
            self.unique_id = unique_id
            return None

        def _abort_if_unique_id_configured(self) -> None:
            if self.unique_id in self._configured_unique_ids:
                raise _AbortFlow("already_configured")

        def _abort_if_unique_id_mismatch(self, reason: str = "unique_id_mismatch") -> None:
            entry = self._reconfigure_entry or self._reauth_entry
            if entry is None:
                raise _UnknownEntry(
                    "_abort_if_unique_id_mismatch called with neither "
                    "_reconfigure_entry nor _reauth_entry set"
                )
            if entry.unique_id != self.unique_id:
                raise _AbortFlow(reason)

        def async_create_entry(self, *, title, data, **k):
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(
            self, *, step_id, data_schema=None, errors=None, description_placeholders=None, **k
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_abort(self, *, reason, **k):
            return {"type": "abort", "reason": reason}

        def async_update_reload_and_abort(
            self,
            entry,
            *,
            data=_UNSET,
            unique_id=_UNSET,
            reason: str = "reconfigure_successful",
            **k,
        ):
            if data is not _UNSET:
                entry.data = data
            if unique_id is not _UNSET:
                entry.unique_id = unique_id
            return {
                "type": "abort",
                "reason": reason,
                "data": entry.data,
                "unique_id": entry.unique_id,
            }

        def _get_reauth_entry(self):
            return self._reauth_entry

        def _get_reconfigure_entry(self):
            return self._reconfigure_entry

    class _OptionsFlow:
        """Minimal stand-in for homeassistant.config_entries.OptionsFlow."""

        def __init__(self, *a, **k) -> None:
            self.config_entry = None

        def async_create_entry(self, *, title, data, **k):
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(
            self, *, step_id, data_schema=None, errors=None, description_placeholders=None, **k
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

    class _OptionsFlowWithReload(_OptionsFlow):
        """Stand-in for OptionsFlowWithReload (reload handled by real HA)."""

    class _ConfigEntry(Generic[_T]):
        """Subscriptable stand-in for ConfigEntry with settable runtime_data."""

        def __init__(self, **k) -> None:
            self.runtime_data = None
            for key, value in k.items():
                setattr(self, key, value)

        def __class_getitem__(cls, item):
            return cls

    ha_ce = _mod(
        "homeassistant.config_entries",
        ConfigEntry=_ConfigEntry,
        OptionsFlowWithReload=_OptionsFlowWithReload,
        ConfigFlow=_ConfigFlow,
        ConfigFlowResult=dict,
        OptionsFlow=_OptionsFlow,
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

    class _EntityCategory:
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    ha_const = _mod("homeassistant.const", Platform=_Platform, EntityCategory=_EntityCategory)
    class _HAError(Exception):
        def __init__(
            self, *args, translation_domain=None, translation_key=None,
            translation_placeholders=None,
        ) -> None:
            super().__init__(*args)
            self.translation_domain = translation_domain
            self.translation_key = translation_key
            self.translation_placeholders = translation_placeholders

    class _ServiceValidationError(_HAError):
        pass

    ha_exc = _mod(
        "homeassistant.exceptions",
        HomeAssistantError=_HAError,
        ServiceValidationError=_ServiceValidationError,
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
    )

    ha_helpers = _mod("homeassistant.helpers")

    class _DUC(Generic[_T]):
        def __init__(self, hass=None, logger=None, *, name="", update_interval=None, **k):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True
            self._listeners: list = []

        def __init_subclass__(cls, **k):
            super().__init_subclass__()

        def async_add_listener(self, update_callback, context=None):
            self._listeners.append(update_callback)

            def _remove() -> None:
                if update_callback in self._listeners:
                    self._listeners.remove(update_callback)

            return _remove

        def async_update_listeners(self) -> None:
            for listener in list(self._listeners):
                listener()

        def async_set_updated_data(self, data) -> None:
            self.data = data

        async def async_request_refresh(self) -> None:
            pass

    class _CE(Generic[_T]):
        def __init__(self, coordinator=None, **k):
            self.coordinator = coordinator

        def __init_subclass__(cls, **k):
            super().__init_subclass__()

        @property
        def available(self) -> bool:
            coordinator = self.coordinator
            if coordinator is None:
                return True
            return getattr(coordinator, "last_update_success", True)

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
    ha_cv = _mod("homeassistant.helpers.config_validation", string=str, boolean=bool,
        config_entry_only_config_schema=lambda domain: (lambda config: config),
    )
    ha_evt = _mod(
        "homeassistant.helpers.event",
        async_call_later=lambda hass, delay, action: (lambda: None),
    )
    ha_dr = _mod(
        "homeassistant.helpers.device_registry",
        DeviceInfo=dict,
        CONNECTION_NETWORK_MAC="mac",
        async_get=MagicMock(),
        async_entries_for_config_entry=MagicMock(return_value=[]),
    )
    ha_typing = _mod("homeassistant.helpers.typing", ConfigType=dict)
    ha_ep = _mod(
        "homeassistant.helpers.entity_platform",
        AddEntitiesCallback=object,
    )

    class _Entity:
        pass

    ha_entity = _mod("homeassistant.helpers.entity", Entity=_Entity)

    class _NumberSelectorConfig:
        def __init__(self, *a, **k) -> None:
            pass

    class _NumberSelector:
        def __init__(self, *a, **k) -> None:
            pass

    class _NumberSelectorMode:
        SLIDER = "slider"

    ha_selector = _mod(
        "homeassistant.helpers.selector",
        NumberSelector=_NumberSelector,
        NumberSelectorConfig=_NumberSelectorConfig,
        NumberSelectorMode=_NumberSelectorMode,
    )

    ha_helpers.update_coordinator = ha_uc
    ha_helpers.aiohttp_client = ha_ac
    ha_helpers.config_validation = ha_cv
    ha_helpers.device_registry = ha_dr
    ha_helpers.event = ha_evt
    ha_helpers.selector = ha_selector
    ha_helpers.entity_platform = ha_ep
    ha_helpers.entity = ha_entity
    ha_helpers.typing = ha_typing

    # --- homeassistant.components.* (only the bits switch/select/diagnostics use) --

    class _SelectEntity:
        pass

    ha_comp_select = _mod("homeassistant.components.select", SelectEntity=_SelectEntity)

    from dataclasses import dataclass

    @dataclass(frozen=True, kw_only=True)
    class _EntityDescription:
        key: str
        name: str | None = None
        icon: str | None = None
        translation_key: str | None = None
        entity_registry_enabled_default: bool = True
        device_class: str | None = None
        entity_category: str | None = None

    @dataclass(frozen=True, kw_only=True)
    class _SwitchEntityDescription(_EntityDescription):
        pass

    class _SwitchEntity:
        pass

    ha_comp_switch = _mod(
        "homeassistant.components.switch",
        SwitchEntity=_SwitchEntity,
        SwitchEntityDescription=_SwitchEntityDescription,
    )

    @dataclass(frozen=True, kw_only=True)
    class _BinarySensorEntityDescription(_EntityDescription):
        pass

    class _BinarySensorEntity:
        pass

    class _BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"
        POWER = "power"
        RUNNING = "running"

    ha_comp_binary_sensor = _mod(
        "homeassistant.components.binary_sensor",
        BinarySensorEntity=_BinarySensorEntity,
        BinarySensorEntityDescription=_BinarySensorEntityDescription,
        BinarySensorDeviceClass=_BinarySensorDeviceClass,
    )

    @dataclass(frozen=True, kw_only=True)
    class _ButtonEntityDescription(_EntityDescription):
        pass

    class _ButtonEntity:
        pass

    class _ButtonDeviceClass:
        RESTART = "restart"

    ha_comp_button = _mod(
        "homeassistant.components.button",
        ButtonEntity=_ButtonEntity,
        ButtonEntityDescription=_ButtonEntityDescription,
        ButtonDeviceClass=_ButtonDeviceClass,
    )

    def _redact(value, to_redact):
        if isinstance(value, dict):
            return {
                k: (
                    "**REDACTED**"
                    if k in to_redact and v is not None
                    else _redact(v, to_redact)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_redact(v, to_redact) for v in value]
        return value

    ha_comp_diagnostics = _mod(
        "homeassistant.components.diagnostics",
        async_redact_data=lambda data, to_redact: _redact(data, to_redact),
        REDACTED="**REDACTED**",
    )

    @dataclass(frozen=True, kw_only=True)
    class _SensorEntityDescription(_EntityDescription):
        state_class: str | None = None
        native_unit_of_measurement: str | None = None

    class _SensorEntity:
        pass

    class _SensorStateClass:
        MEASUREMENT = "measurement"

    ha_comp_sensor = _mod(
        "homeassistant.components.sensor",
        SensorEntity=_SensorEntity,
        SensorEntityDescription=_SensorEntityDescription,
        SensorStateClass=_SensorStateClass,
    )

    ha_components = _mod("homeassistant.components")
    ha_components.sensor = ha_comp_sensor
    ha_components.select = ha_comp_select
    ha_components.switch = ha_comp_switch
    ha_components.binary_sensor = ha_comp_binary_sensor
    ha_components.button = ha_comp_button
    ha_components.diagnostics = ha_comp_diagnostics

    # Exposed as an attribute on the top-level `ha` module too, matching how
    # e.g. `ha_helpers.selector = ha_selector` exposes `selector` above --
    # sys.modules registration alone (below) doesn't set this automatically
    # since these stubs bypass the normal import machinery.
    ha.data_entry_flow = ha_daf

    modules = {
        "homeassistant": ha,
        "homeassistant.core": ha_core,
        "homeassistant.config_entries": ha_ce,
        "homeassistant.const": ha_const,
        "homeassistant.data_entry_flow": ha_daf,
        "homeassistant.exceptions": ha_exc,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
        "homeassistant.helpers.aiohttp_client": ha_ac,
        "homeassistant.helpers.config_validation": ha_cv,
        "homeassistant.helpers.device_registry": ha_dr,
        "homeassistant.helpers.event": ha_evt,
        "homeassistant.helpers.selector": ha_selector,
        "homeassistant.helpers.entity_platform": ha_ep,
        "homeassistant.helpers.entity": ha_entity,
        "homeassistant.helpers.typing": ha_typing,
        "homeassistant.components": ha_components,
        "homeassistant.components.select": ha_comp_select,
        "homeassistant.components.switch": ha_comp_switch,
        "homeassistant.components.binary_sensor": ha_comp_binary_sensor,
        "homeassistant.components.button": ha_comp_button,
        "homeassistant.components.sensor": ha_comp_sensor,
        "homeassistant.components.diagnostics": ha_comp_diagnostics,
    }
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


_stub_voluptuous()
_stub_homeassistant()
