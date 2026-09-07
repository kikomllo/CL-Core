import logging
import sys
from typing import Dict, List, Optional

SYSTEM_DEFAULT = "System Default"

# Windows Core Audio exposes a device's raw, manufacturer-reported hardware
# name as a property SEPARATE from the localized "Category (hardware name)"
# string PortAudio/SDL2 report -- e.g. the same G435 headset is
# "Microphone (G435 Wireless Gaming Headset)" in English or "Microfone
# (G435 Wireless Gaming Headset)" in Portuguese, but PKEY_DeviceInterface_
# FriendlyName is always just "G435 Wireless Gaming Headset" regardless of
# Windows' display language -- no per-language word list needed.
_DEVPKEY_DEVICE_FRIENDLY_NAME = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 14"
_DEVPKEY_INTERFACE_FRIENDLY_NAME = "{026E516E-B814-414B-83CD-856D6FEF4822} 2"

# PortAudio on Windows enumerates the same physical microphone once per host
# API (MME, DirectSound, WASAPI, WDM-KS) -- a raw listing shows every device
# several times over (some truncated to ~31 chars by MME, some under a
# WDM-KS kernel-streaming path that doesn't even match the name shown
# elsewhere), plus WDM-KS-only entries (Stereo Mix, raw Bluetooth HFP
# endpoints, etc.) that no consumer app surfaces as a "pick your microphone"
# option. WASAPI is what Discord/browsers/Zoom actually enumerate against on
# Windows, so restricting to it here matches that and needs no dedup at all.
# Linux's PortAudio backend (ALSA) doesn't have this multi-host-API
# duplication problem, so the filter is Windows-only.
_REQUIRED_WINDOWS_HOST_API = "Windows WASAPI"


def _host_api_name(pa, device_info) -> str:
    try:
        return pa.get_host_api_info_by_index(device_info["hostApi"])["name"]
    except Exception:
        return ""


def _is_allowed_host_api(pa, device_info) -> bool:
    if sys.platform != "win32":
        return True
    return _host_api_name(pa, device_info) == _REQUIRED_WINDOWS_HOST_API


def list_input_device_names() -> List[str]:
    """Enumerates input-capable devices via PortAudio, restricted on Windows
    to the WASAPI host API (see module docstring above)."""
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
        return [
            pa.get_device_info_by_index(i)["name"]
            for i in range(pa.get_device_count())
            if pa.get_device_info_by_index(i).get("maxInputChannels", 0) > 0
            and _is_allowed_host_api(pa, pa.get_device_info_by_index(i))
        ]
    except Exception as e:
        logging.error(f"Failed to enumerate input audio devices: {e}")
        return []
    finally:
        pa.terminate()


def resolve_input_device_index(pa, device_name: Optional[str]) -> Optional[int]:
    """Maps a device name back to a PortAudio index for an already-open
    PyAudio instance, restricted the same way list_input_device_names() is."""
    if not device_name or device_name == SYSTEM_DEFAULT:
        return None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("name") == device_name and info.get("maxInputChannels", 0) > 0 and _is_allowed_host_api(pa, info):
            return i
    logging.warning(f"Input device '{device_name}' not found; falling back to system default.")
    return None


def resolve_capture_device_index(pa, device_name: Optional[str]) -> Optional[int]:
    """Like resolve_input_device_index(), but for opening the capture stream
    rather than for display. When no device_name is given at all, this
    intentionally returns None so the caller opens with no index and lets
    PortAudio pick its own default the way the mic always did before any
    explicit device selection existed -- confirmed live that this default
    resolves to MME, not WASAPI, and accepts 16kHz directly (no manual
    resample needed at all) even on a device whose WASAPI endpoint rejects
    it outright.

    For an EXPLICITLY named device, prefer its MME entry over WASAPI for
    the same reason. DirectSound was tried here first and reverted: live
    testing showed it produce wildly inconsistent captures on this machine
    (full-scale noise on one open, near-silence on the next, identical
    device and settings) -- actively broken, worse than paying for the
    manual resample. MME did not show that problem across repeated opens.
    MME truncates names to ~31 chars, so a match there is a prefix check
    against the full (WASAPI-sourced) device_name rather than an exact one."""
    if not device_name or device_name == SYSTEM_DEFAULT:
        return None
    if sys.platform != "win32":
        return resolve_input_device_index(pa, device_name)

    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) <= 0 or _host_api_name(pa, info) != "MME":
            continue
        name = info.get("name", "")
        if name and (name == device_name or device_name.startswith(name)):
            return i

    return resolve_input_device_index(pa, device_name)


def list_output_device_names() -> List[str]:
    """Enumerates playback devices via SDL2 (pygame). Cross-platform, no CURRENT_OS branch needed."""
    import pygame
    pygame.init()
    try:
        import pygame._sdl2.audio as sdl2_audio
        return list(sdl2_audio.get_audio_device_names(False))
    except Exception as e:
        logging.error(f"Failed to enumerate output audio devices: {e}")
        return []
    finally:
        pygame.quit()


def _pycaw_raw_names_by_full_name(kind: str) -> Dict[str, str]:
    """{full localized name: raw hardware interface name} for every Windows
    Core Audio endpoint of the given direction. Windows-only; returns {}
    anywhere this can't be queried (non-Windows, pycaw/COM failure)."""
    if sys.platform != "win32":
        return {}
    try:
        import warnings
        from pycaw.pycaw import AudioUtilities
        from pycaw.constants import EDataFlow
        data_flow = EDataFlow.eCapture.value if kind == "input" else EDataFlow.eRender.value
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            devices = AudioUtilities.GetAllDevices(data_flow=data_flow)
        result = {}
        for d in devices:
            full = d.properties.get(_DEVPKEY_DEVICE_FRIENDLY_NAME)
            raw = d.properties.get(_DEVPKEY_INTERFACE_FRIENDLY_NAME)
            if full and raw:
                result[full] = raw
        return result
    except Exception as e:
        logging.warning(f"Failed to read raw hardware names via pycaw: {e}")
        return {}


def get_clean_display_names(names: List[str], kind: str) -> Dict[str, str]:
    """{actual device name -> display name} for a list of names as returned
    by list_input_device_names()/list_output_device_names(). Uses the raw
    hardware name (see module docstring) wherever it's unique among this
    list -- true for essentially every standalone peripheral (a USB
    headset, mic, or virtual cable). Falls back to the original full name
    when the raw name is shared by more than one device in the list: an
    onboard multi-jack chip's several endpoints (Front/Rear/Mic/Line-In/
    Stereo Mix, all "Realtek High Definition Audio") or a GPU's several
    HDMI/monitor outputs (all "NVIDIA High Definition Audio") would
    otherwise become indistinguishable, since the raw name is the shared
    chipset, and the part that's actually specific to each is the prefix
    the raw name would have dropped."""
    raw_by_full = _pycaw_raw_names_by_full_name(kind)
    if not raw_by_full:
        return {name: name for name in names}

    raw_names = [raw_by_full.get(name) for name in names]
    counts: Dict[str, int] = {}
    for raw in raw_names:
        if raw:
            counts[raw] = counts.get(raw, 0) + 1

    return {
        name: raw if raw and counts.get(raw) == 1 else name
        for name, raw in zip(names, raw_names)
    }
