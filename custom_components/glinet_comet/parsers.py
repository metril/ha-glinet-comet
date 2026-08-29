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
    return _as_str(
        first_path(data, "glinet.upgrade_version.model", "info.system.platform.model")
    )


def firmware_version(data: dict[str, Any]) -> str | None:
    """Return the installed firmware version string."""
    return _as_str(
        first_path(
            data,
            "glinet.upgrade_version.version",
            "glinet.upgrade_compare.local_version",
        )
    )


def serial(data: dict[str, Any]) -> str | None:
    """Return the device serial number."""
    return _as_str(_dig(data, "info.system.platform.serial"))


def mac_address(data: dict[str, Any]) -> str | None:
    """Return the device's network MAC address."""
    return _as_str(
        first_path(
            data, "glinet.network.config.mac_address", "glinet.network.mac_address"
        )
    )


def _as_bool(value: Any) -> bool | None:
    """Return ``value`` if it's a real bool, else ``None`` (rejects 0/1/etc.)."""
    return value if isinstance(value, bool) else None


def _as_int(value: Any) -> int | None:
    """Return ``value`` if it's a real int (not a bool), else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _as_str(value: Any) -> str | None:
    """Return ``value`` if it's a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None


# --- ATX ----------------------------------------------------------------


def atx_enabled(data: dict[str, Any]) -> bool | None:
    """Return whether an ATX board is attached/enabled on this unit."""
    return _as_bool(_dig(data, "atx.enabled"))


def atx_busy(data: dict[str, Any]) -> bool | None:
    """Return whether the ATX subsystem is currently busy."""
    return _as_bool(_dig(data, "atx.busy"))


def atx_power_on(data: dict[str, Any]) -> bool | None:
    """Return True/False for ``atx.power`` (``"on"``/``"off"``), None if missing."""
    value = _dig(data, "atx.power")
    if not isinstance(value, str):
        return None
    return value == "on"


# --- Video / streamer -----------------------------------------------------
# ``streamer.streamer`` is the live subsystem snapshot and can be ``null``
# (streamer subsystem down/idle) -- every helper here must null-check it.


def streamer_running(data: dict[str, Any]) -> bool:
    """Return whether the streamer subsystem is currently up."""
    return isinstance(_dig(data, "streamer.streamer"), dict)


def hdmi_signal(data: dict[str, Any]) -> bool | None:
    """Return HDMI input signal presence, preferring ``hdmi.signal``."""
    streamer = _dig(data, "streamer.streamer")
    if not isinstance(streamer, dict):
        return None
    signal = _as_bool(_dig(streamer, "hdmi.signal"))
    if signal is not None:
        return signal
    return _as_bool(_dig(streamer, "source.online"))


def hdmi_out_signal(data: dict[str, Any]) -> bool | None:
    """Return whether the HDMI output signal is present."""
    streamer = _dig(data, "streamer.streamer")
    if not isinstance(streamer, dict):
        return None
    return _as_bool(_dig(streamer, "hdmi.out_signal"))


def video_resolution(data: dict[str, Any]) -> str | None:
    """Return the current capture resolution as ``"{width}x{height}"``."""
    streamer = _dig(data, "streamer.streamer")
    if not isinstance(streamer, dict):
        return None
    width = _as_int(_dig(streamer, "source.resolution.width"))
    height = _as_int(_dig(streamer, "source.resolution.height"))
    if width and height:
        return f"{width}x{height}"
    real = first_path(streamer, "real_resolution", "source.real_resolution")
    if isinstance(real, str) and real:
        return real.split("@")[0] or None
    return None


def video_fps_target(data: dict[str, Any]) -> int | None:
    """Return the configured target FPS (``streamer.params.desired_fps``)."""
    return _as_int(_dig(data, "streamer.params.desired_fps"))


# --- HID --------------------------------------------------------------------


def keyboard_online(data: dict[str, Any]) -> bool | None:
    """Return whether the emulated keyboard is online."""
    return _as_bool(_dig(data, "hid.keyboard.online"))


def mouse_online(data: dict[str, Any]) -> bool | None:
    """Return whether the emulated mouse is online."""
    return _as_bool(_dig(data, "hid.mouse.online"))


def hid_online(data: dict[str, Any]) -> bool | None:
    """Return whether the HID subsystem overall is online."""
    return _as_bool(_dig(data, "hid.online"))


def hid_connected(data: dict[str, Any]) -> bool | None:
    """Return whether the HID USB gadget is connected to the host."""
    return _as_bool(_dig(data, "hid.connected"))


def jiggler_enabled(data: dict[str, Any]) -> bool | None:
    """Return whether the mouse jiggler is enabled.

    ``hid.jiggler`` is normally a dict with an ``enabled`` key, but a bare
    bool is also accepted for robustness across firmware variants.
    """
    jiggler = _dig(data, "hid.jiggler")
    if isinstance(jiggler, bool):
        return jiggler
    if isinstance(jiggler, dict):
        return _as_bool(jiggler.get("enabled"))
    return None


def jiggler_active(data: dict[str, Any]) -> bool | None:
    """Return whether the mouse jiggler is currently actively moving the mouse."""
    jiggler = _dig(data, "hid.jiggler")
    if not isinstance(jiggler, dict):
        return None
    return _as_bool(jiggler.get("active"))


def jiggler_interval(data: dict[str, Any]) -> int | None:
    """Return the jiggler's configured interval, in seconds."""
    jiggler = _dig(data, "hid.jiggler")
    if not isinstance(jiggler, dict):
        return None
    return _as_int(jiggler.get("interval"))


# --- MSD (mass storage device) ----------------------------------------------


def msd_enabled(data: dict[str, Any]) -> bool | None:
    """Return whether the virtual mass-storage device is enabled."""
    return _as_bool(_dig(data, "msd.enabled"))


def msd_online(data: dict[str, Any]) -> bool | None:
    """Return whether the MSD subsystem is online."""
    return _as_bool(_dig(data, "msd.online"))


def msd_busy(data: dict[str, Any]) -> bool | None:
    """Return whether the MSD subsystem is currently busy."""
    return _as_bool(_dig(data, "msd.busy"))


def msd_connected(data: dict[str, Any]) -> bool | None:
    """Return whether the virtual drive is connected to the target host."""
    value = first_path(data, "msd.drive.connected", "msd.connected")
    return _as_bool(value)


def msd_image(data: dict[str, Any]) -> str | None:
    """Return the name of the currently mounted image, if any.

    ``msd.drive.image`` has been observed as ``null`` when nothing is
    mounted; a plain string or a dict carrying a ``name`` key are both
    handled defensively in case a firmware variant nests it differently.
    """
    value = _dig(data, "msd.drive.image")
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return _as_str(value.get("name"))
    return None


def msd_cdrom(data: dict[str, Any]) -> bool | None:
    """Return whether the mounted image is exposed as a CD-ROM."""
    return _as_bool(_dig(data, "msd.drive.cdrom"))


def msd_rw(data: dict[str, Any]) -> bool | None:
    """Return whether the mounted image is exposed read-write."""
    return _as_bool(_dig(data, "msd.drive.rw"))


def msd_images(data: dict[str, Any]) -> list[str]:
    """Return the sorted names of images available in storage."""
    images = _dig(data, "msd.storage.images")
    if not isinstance(images, dict):
        return []
    return sorted(images.keys())


# --- System -------------------------------------------------------------


def kvmd_version(data: dict[str, Any]) -> str | None:
    """Return the kvmd fork version string (e.g. ``"4.82"``)."""
    return _as_str(_dig(data, "info.system.kvmd.version"))


def hostname(data: dict[str, Any]) -> str | None:
    """Return the device's configured hostname."""
    value = _dig(data, "glinet.hostname")
    if isinstance(value, dict):
        return _as_str(value.get("hostname"))
    if isinstance(value, str):
        return value or None
    return None


def ip_address(data: dict[str, Any]) -> str | None:
    """Return the device's current IP address."""
    value = first_path(
        data, "glinet.network.config.ip_address", "glinet.network.ip_address"
    )
    return _as_str(value)


def network_state(data: dict[str, Any]) -> str | None:
    """Return the network link state (e.g. ``"online"``)."""
    return _as_str(_dig(data, "glinet.network.config.state"))


def is_dhcp(data: dict[str, Any]) -> bool | None:
    """Return whether the network interface is configured via DHCP."""
    return _as_bool(_dig(data, "glinet.network.config.is_dhcp"))


# --- Firmware -------------------------------------------------------------


def firmware_latest_version(data: dict[str, Any]) -> str | None:
    """Return the latest firmware version known to the device (server-side)."""
    server = _as_str(_dig(data, "glinet.upgrade_compare.server_version"))
    if server is not None:
        return server
    return firmware_version(data)


def firmware_update_available(data: dict[str, Any]) -> bool | None:
    """Return whether a firmware update is available (server != local version)."""
    compare = _dig(data, "glinet.upgrade_compare")
    if not isinstance(compare, dict):
        return None
    local = _as_str(compare.get("local_version"))
    server = _as_str(compare.get("server_version"))
    if local is None or server is None:
        return None
    return server != local


def firmware_beta_version(data: dict[str, Any]) -> str | None:
    """Return the available beta firmware version, if any."""
    return _as_str(_dig(data, "glinet.upgrade_compare.beta_version"))


def firmware_release_notes(data: dict[str, Any]) -> str | None:
    """Return the release notes, whitespace-collapsed and capped at 255 chars.

    255 matches Home Assistant's ``update`` entity ``release_summary`` limit.
    """
    value = _dig(data, "glinet.upgrade_compare.release_note")
    if not isinstance(value, str) or not value:
        return None
    collapsed = " ".join(value.split())
    return collapsed[:255] or None


def firmware_compare_error(data: dict[str, Any]) -> str | None:
    """Return the error reported by the last upgrade-compare check, if any."""
    return _as_str(_dig(data, "glinet.upgrade_compare.error"))
