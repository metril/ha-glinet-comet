"""Remove stale ATX entities from the entity registry when ``enable_atx`` is off."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .data import CometConfigEntry

# Per-port ATX unique_id suffixes (``<entry>_switch_port_<ch>_<suffix>``) and
# top-level ones (``<entry>_<key>``) for a given platform domain.
_TOP = {
    "button": ("atx_power", "atx_power_long", "atx_reset"),
    "binary_sensor": ("atx_power", "atx_hdd_activity"),
}
_PORT = {
    "button": ("atx_power", "atx_power_long", "atx_reset"),
    "binary_sensor": ("power", "hdd"),
}


def async_remove_atx_entities(
    hass: HomeAssistant, entry: CometConfigEntry, domain: str
) -> None:
    """Drop this entry's registered ATX entities of ``domain``."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    top = {f"{prefix}{k}" for k in _TOP[domain]}
    port_prefix = f"{prefix}switch_port_"
    for ent in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if ent.domain != domain or not ent.unique_id:
            continue
        uid = ent.unique_id
        if uid in top or (
            uid.startswith(port_prefix)
            and uid[len(port_prefix):].partition("_")[2] in _PORT[domain]
        ):
            registry.async_remove(ent.entity_id)
