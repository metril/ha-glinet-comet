"""Runtime data types for the GL.iNet Comet integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .api import CometApiClient
from .coordinator import CometDataUpdateCoordinator


@dataclass
class CometRuntimeData:
    """Objects shared by a loaded config entry."""

    coordinator: CometDataUpdateCoordinator
    client: CometApiClient


type CometConfigEntry = ConfigEntry[CometRuntimeData]
