"""Windows-only per-app default audio endpoint override.

Run standalone (see clTerminal.py's _set_app_output_device_windows) rather
than called inline from the long-running clTerminal.py daemon: this makes
a single, isolated call into an undocumented WinRT runtime class
("Windows.Media.Internal.AudioPolicyConfig") that Windows' own Settings
app uses internally for its "App volume and device preferences" panel,
and that Microsoft could change without notice in a future update. There
is no public/documented Win32 API for this at all -- this is the same
private interface reverse-engineered by the open-source EarTrumpet and
SoundSwitch volume mixers, verified against both of their implementations
independently before writing this. Keeping the raw COM/vtable poking in
its own throwaway subprocess means a bad interaction with that interface
can only ever crash this one-shot process, never the daemon.

Usage: python clAppAudioRouting.py <pid> <device_id_or_empty>
    pid: the target process id (an integer).
    device_id_or_empty: the plain Core Audio render-endpoint id (as
        pycaw's AudioDevice.id reports it) to route this process's output
        to, or an empty string to clear the override and fall back to
        the system default.
Prints "OK" and exits 0 on success, "ERROR: <message>" and exits 1 otherwise.

Functions below are defined unconditionally (matching clUtilities.py's
Windows-only functions) so this module imports and unit-tests cleanly on
Linux too -- only actually calling them off Windows fails, the same way
shelling out to schtasks.exe would."""
import sys
import ctypes

DEVINTERFACE_AUDIO_RENDER = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}"
MMDEVAPI_TOKEN = r"\\?\SWD#MMDEVAPI#"
ACTIVATABLE_CLASS_ID = "Windows.Media.Internal.AudioPolicyConfig"

EDATAFLOW_ERENDER = 0
EROLE_ECONSOLE = 0
EROLE_EMULTIMEDIA = 1

# Vtable slot offsets -- cross-checked against two independent open-source
# implementations (EarTrumpet and SoundSwitch) that both arrive at the same
# numbers: IUnknown's 3 slots + IInspectable's 3 slots + 19 unrelated
# methods this runtime class also exposes (chat-app/ringer/volume-group
# APIs, irrelevant here) land GetIids at slot 3 and
# SetPersistedDefaultAudioEndpoint at slot 25.
_SLOT_QUERY_INTERFACE = 0
_SLOT_RELEASE = 2
_SLOT_GET_IIDS = 3
_SLOT_SET_PERSISTED_DEFAULT = 25


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class HResultError(Exception):
    pass


def build_wrapped_device_id(raw_endpoint_id):
    """Wraps a plain Core Audio render-endpoint id (pycaw's AudioDevice.id)
    into the device-interface-path form this specific WinRT call expects --
    a plain MMDevice endpoint id is rejected outright. None means "clear
    the override", not "use this literal string"."""
    if not raw_endpoint_id:
        return None
    return f"{MMDEVAPI_TOKEN}{raw_endpoint_id}{DEVINTERFACE_AUDIO_RENDER}"


def _check(hr, what):
    if hr != 0:
        raise HResultError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def _vtable_slot(obj_ptr, slot):
    vtable_ptr = ctypes.cast(obj_ptr, ctypes.POINTER(ctypes.c_void_p))[0]
    return ctypes.cast(vtable_ptr, ctypes.POINTER(ctypes.c_void_p))[slot]


def _create_hstring(value):
    if not value:
        return None
    combase = ctypes.WinDLL("combase.dll")
    h = ctypes.c_void_p()
    hr = combase.WindowsCreateString(value, len(value), ctypes.byref(h))
    _check(hr, "WindowsCreateString")
    return h


def _delete_hstring(h):
    if h:
        ctypes.WinDLL("combase.dll").WindowsDeleteString(h)


def _release(obj_ptr):
    if not obj_ptr:
        return
    proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    proto(_vtable_slot(obj_ptr, _SLOT_RELEASE))(obj_ptr)


def _get_iids(obj_ptr):
    proto = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.POINTER(_GUID)),
    )
    func = proto(_vtable_slot(obj_ptr, _SLOT_GET_IIDS))
    count = ctypes.c_uint32(0)
    iids_ptr = ctypes.POINTER(_GUID)()
    hr = func(obj_ptr, ctypes.byref(count), ctypes.byref(iids_ptr))
    _check(hr, "GetIids")
    guids = [iids_ptr[i] for i in range(count.value)]
    ctypes.windll.ole32.CoTaskMemFree(iids_ptr)
    return guids


def _query_interface(obj_ptr, iid):
    proto = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    func = proto(_vtable_slot(obj_ptr, _SLOT_QUERY_INTERFACE))
    out_ptr = ctypes.c_void_p()
    hr = func(obj_ptr, ctypes.byref(iid), ctypes.byref(out_ptr))
    _check(hr, "QueryInterface")
    return out_ptr


def _activate_audio_policy_config():
    class_id = _create_hstring(ACTIVATABLE_CLASS_ID)
    try:
        audioses = ctypes.WinDLL("audioses.dll")
        factory_ptr = ctypes.c_void_p()
        hr = audioses.DllGetActivationFactory(class_id, ctypes.byref(factory_ptr))
        _check(hr, "DllGetActivationFactory")
        return factory_ptr
    finally:
        _delete_hstring(class_id)


def set_persisted_default_audio_endpoint(pid, raw_endpoint_id):
    """Routes process `pid`'s render (output) audio to the endpoint
    identified by `raw_endpoint_id` (a plain pycaw AudioDevice.id), or
    clears the override back to the system default when falsy. Sets both
    eConsole and eMultimedia roles, matching what Windows' own Settings
    app does for a single per-app device choice."""
    factory_ptr = _activate_audio_policy_config()
    try:
        guids = _get_iids(factory_ptr)
        if not guids:
            raise HResultError("AudioPolicyConfig reported no interface IIDs")
        iface_ptr = _query_interface(factory_ptr, guids[-1])
        try:
            device_id = build_wrapped_device_id(raw_endpoint_id)
            device_hstring = _create_hstring(device_id) if device_id else None
            try:
                proto = ctypes.WINFUNCTYPE(
                    ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32,
                    ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                )
                func = proto(_vtable_slot(iface_ptr, _SLOT_SET_PERSISTED_DEFAULT))
                for role in (EROLE_ECONSOLE, EROLE_EMULTIMEDIA):
                    hr = func(iface_ptr, pid, EDATAFLOW_ERENDER, role, device_hstring)
                    _check(hr, f"SetPersistedDefaultAudioEndpoint(role={role})")
            finally:
                _delete_hstring(device_hstring)
        finally:
            _release(iface_ptr)
    finally:
        _release(factory_ptr)


def main(argv):
    if len(argv) != 3:
        print("ERROR: usage: clAppAudioRouting.py <pid> <device_id_or_empty>")
        return 1
    try:
        pid = int(argv[1])
    except ValueError:
        print("ERROR: pid must be an integer")
        return 1
    if sys.platform != "win32":
        print("ERROR: per-app output device routing is Windows-only")
        return 1
    try:
        set_persisted_default_audio_endpoint(pid, argv[2])
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
