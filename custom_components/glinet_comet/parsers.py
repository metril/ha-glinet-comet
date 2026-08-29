"""Field extraction from the GL.iNet Comet coordinator state.

The exact key layout can vary across firmware revisions, so every value an
entity reads is extracted here via a list of candidate dotted paths with
fallbacks -- one place to adjust if a field path differs on a given unit.
Each helper returns ``None`` (never raises) when nothing matches, so entities
show ``unknown`` rather than crashing. This module has no Home Assistant
imports -- it operates purely on the coordinator's ``dict`` state.
"""

from __future__ import annotations

from typing import Any


def _dig(data: Any, path: str) -> Any:
    """Follow a dotted path; list indices allowed as numbers (e.g. ``a.0.b``)."""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def first_path(data: Any, *paths: str) -> Any:
    """Return the first non-None value among the candidate dotted paths."""
    for path in paths:
        value = _dig(data, path)
        if value is not None:
            return value
    return None


# --- Device identity ---------------------------------------------------------
# ``glinet.*`` holds the raw ``result`` bodies of the GL.iNet-specific reads
# (hostname/network/upgrade_version/upgrade_compare); ``info.*`` holds the
# merged kvmd ``info`` sections (system/auth/meta/extras/hw).


def device_model(data: dict[str, Any]) -> str | None:
    """Return the device model string (e.g. ``RM1PE``)."""
    value = first_path(
        data, "glinet.upgrade_version.model", "info.system.platform.model"
    )
    return str(value) if value is not None else None


def firmware_version(data: dict[str, Any]) -> str | None:
    """Return the installed firmware version string."""
    value = first_path(
        data,
        "glinet.upgrade_version.version",
        "glinet.upgrade_compare.local_version",
    )
    return str(value) if value is not None else None


def serial(data: dict[str, Any]) -> str | None:
    """Return the device serial number."""
    value = _dig(data, "info.system.platform.serial")
    return str(value) if value is not None else None


def mac_address(data: dict[str, Any]) -> str | None:
    """Return the device's network MAC address."""
    value = first_path(
        data, "glinet.network.config.mac_address", "glinet.network.mac_address"
    )
    return str(value) if value is not None else None
