import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clAudioDevices import list_input_device_names, resolve_input_device_index, list_output_device_names, get_clean_display_names


def _fake_pa(devices, host_apis=("Windows WASAPI",)):
    """devices: dicts with name/maxInputChannels/hostApi (index into host_apis,
    defaults to 0 -- the single-host-API case most tests don't care about)."""
    pa = MagicMock()
    pa.get_device_count.return_value = len(devices)
    pa.get_device_info_by_index.side_effect = lambda i: {"hostApi": 0, **devices[i]}
    pa.get_host_api_info_by_index.side_effect = lambda i: {"name": host_apis[i]}
    return pa


class TestListInputDeviceNames:
    def test_filters_to_input_capable_devices_only(self):
        devices = [
            {"name": "Speakers", "maxInputChannels": 0},
            {"name": "USB Mic", "maxInputChannels": 1},
        ]
        with patch('pyaudio.PyAudio', return_value=_fake_pa(devices)), patch('sys.platform', 'win32'):
            names = list_input_device_names()
        assert names == ["USB Mic"]

    def test_terminates_the_throwaway_pyaudio_instance(self):
        pa = _fake_pa([])
        with patch('pyaudio.PyAudio', return_value=pa):
            list_input_device_names()
        pa.terminate.assert_called_once()


class TestWindowsHostApiFiltering:
    """PortAudio on Windows enumerates the same physical mic once per host
    API (MME, DirectSound, WASAPI, WDM-KS) -- a live machine showed
    'Microphone (Razer Seiren Mini)' four times over, some truncated by MME,
    plus WDM-KS-only technical endpoints (Stereo Mix, raw Bluetooth paths)
    that Discord/browsers never show as microphone choices. Restricting to
    WASAPI (what those apps actually enumerate against) fixes both at once."""

    HOST_APIS = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")

    def test_only_wasapi_devices_are_listed_on_windows(self):
        devices = [
            {"name": "Microphone (Razer Seiren Mini)", "maxInputChannels": 1, "hostApi": 0},  # MME
            {"name": "Microphone (Razer Seiren Mini)", "maxInputChannels": 1, "hostApi": 1},  # DirectSound
            {"name": "Microphone (Razer Seiren Mini)", "maxInputChannels": 1, "hostApi": 2},  # WASAPI
            {"name": "Microphone (Razer Seiren Mini)", "maxInputChannels": 1, "hostApi": 3},  # WDM-KS
            {"name": "Stereo Mix (Realtek HD Audio Stereo input)", "maxInputChannels": 1, "hostApi": 3},  # WDM-KS only
        ]
        with patch('pyaudio.PyAudio', return_value=_fake_pa(devices, self.HOST_APIS)), patch('sys.platform', 'win32'):
            names = list_input_device_names()
        assert names == ["Microphone (Razer Seiren Mini)"]

    def test_mme_truncated_duplicate_is_dropped_not_shown_as_distinct(self):
        devices = [
            {"name": "Microphone (G435 Wireless Gamin", "maxInputChannels": 1, "hostApi": 0},  # MME, truncated
            {"name": "Microphone (G435 Wireless Gaming Headset)", "maxInputChannels": 1, "hostApi": 2},  # WASAPI, full
        ]
        with patch('pyaudio.PyAudio', return_value=_fake_pa(devices, self.HOST_APIS)), patch('sys.platform', 'win32'):
            names = list_input_device_names()
        assert names == ["Microphone (G435 Wireless Gaming Headset)"]

    def test_wasapi_only_device_with_no_wasapi_entry_is_not_shown(self):
        """A device that only exists via WDM-KS (e.g. Stereo Mix) is
        intentionally excluded on Windows -- it's not a real microphone
        choice a consumer app would offer."""
        devices = [{"name": "Stereo Mix (Realtek HD Audio Stereo input)", "maxInputChannels": 1, "hostApi": 3}]
        with patch('pyaudio.PyAudio', return_value=_fake_pa(devices, self.HOST_APIS)), patch('sys.platform', 'win32'):
            names = list_input_device_names()
        assert names == []

    def test_resolve_only_matches_the_wasapi_entry(self):
        devices = [
            {"name": "Microphone (Razer Seiren Mini)", "maxInputChannels": 1, "hostApi": 0},  # MME
            {"name": "Microphone (Razer Seiren Mini)", "maxInputChannels": 1, "hostApi": 2},  # WASAPI
        ]
        pa = _fake_pa(devices, self.HOST_APIS)
        with patch('sys.platform', 'win32'):
            assert resolve_input_device_index(pa, "Microphone (Razer Seiren Mini)") == 1

    def test_non_windows_platforms_are_not_filtered_by_host_api(self):
        """Linux's PortAudio backend (ALSA) doesn't have this multi-host-API
        duplication problem -- the WASAPI restriction must not apply there."""
        devices = [{"name": "USB Mic", "maxInputChannels": 1, "hostApi": 0}]  # "MME" in HOST_APIS, but platform isn't win32
        with patch('pyaudio.PyAudio', return_value=_fake_pa(devices, self.HOST_APIS)), patch('sys.platform', 'linux'):
            names = list_input_device_names()
        assert names == ["USB Mic"]


class TestResolveInputDeviceIndex:
    def test_system_default_returns_none(self):
        pa = _fake_pa([{"name": "USB Mic", "maxInputChannels": 1}])
        assert resolve_input_device_index(pa, None) is None
        assert resolve_input_device_index(pa, "System Default") is None

    def test_matches_by_exact_name(self):
        devices = [
            {"name": "Speakers", "maxInputChannels": 0},
            {"name": "USB Mic", "maxInputChannels": 1},
        ]
        pa = _fake_pa(devices)
        with patch('sys.platform', 'win32'):
            assert resolve_input_device_index(pa, "USB Mic") == 1

    def test_unresolvable_name_falls_back_to_none(self):
        pa = _fake_pa([{"name": "USB Mic", "maxInputChannels": 1}])
        with patch('sys.platform', 'win32'):
            assert resolve_input_device_index(pa, "Unplugged Mic") is None


class TestResolveCaptureDeviceIndex:
    """Opening the capture stream deliberately prefers MME over WASAPI for
    an explicitly-named device: WASAPI's shared-mode stream rejects a rate
    that doesn't match the device's mix format outright, forcing clMic.py's
    own manual resample fallback, which measurably hurt accuracy
    (openWakeWord's confidence collapsed to near-zero on audio Whisper
    still tolerated). MME lets Windows' own audio engine convert the rate
    transparently on open instead -- confirmed live and, unlike
    DirectSound (tried first and reverted for producing inconsistent
    captures on repeated opens), stable across repeated opens of the same
    device. No device_name at all (the plain System Default case) opens
    with no index, matching what the mic always did before explicit
    per-device selection existed."""

    HOST_APIS = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")

    def test_prefers_mme_over_wasapi_for_the_same_device(self):
        from utils.clAudioDevices import resolve_capture_device_index
        devices = [
            {"name": "Microphone (G435 Wireless Gaming Headset)", "maxInputChannels": 1, "hostApi": 0},  # MME
            {"name": "Microphone (G435 Wireless Gaming Headset)", "maxInputChannels": 2, "hostApi": 2},  # WASAPI
        ]
        pa = _fake_pa(devices, self.HOST_APIS)
        with patch('sys.platform', 'win32'):
            assert resolve_capture_device_index(pa, "Microphone (G435 Wireless Gaming Headset)") == 0

    def test_matches_mme_by_truncated_name_prefix(self):
        from utils.clAudioDevices import resolve_capture_device_index
        devices = [
            {"name": "Microphone (G435 Wireless Gamin", "maxInputChannels": 1, "hostApi": 0},  # MME, truncated
            {"name": "Microphone (G435 Wireless Gaming Headset)", "maxInputChannels": 2, "hostApi": 2},  # WASAPI
        ]
        pa = _fake_pa(devices, self.HOST_APIS)
        with patch('sys.platform', 'win32'):
            assert resolve_capture_device_index(pa, "Microphone (G435 Wireless Gaming Headset)") == 0

    def test_does_not_match_directsound(self):
        """DirectSound is deliberately never preferred here -- see the class
        docstring on why it was tried and reverted."""
        from utils.clAudioDevices import resolve_capture_device_index
        devices = [
            {"name": "USB Mic", "maxInputChannels": 1, "hostApi": 1},  # DirectSound
            {"name": "USB Mic", "maxInputChannels": 1, "hostApi": 2},  # WASAPI
        ]
        pa = _fake_pa(devices, self.HOST_APIS)
        with patch('sys.platform', 'win32'):
            assert resolve_capture_device_index(pa, "USB Mic") == 1  # falls back to WASAPI, not DirectSound

    def test_falls_back_to_wasapi_index_when_no_mme_entry(self):
        from utils.clAudioDevices import resolve_capture_device_index
        devices = [{"name": "USB Mic", "maxInputChannels": 1, "hostApi": 2}]  # WASAPI only
        pa = _fake_pa(devices, self.HOST_APIS)
        with patch('sys.platform', 'win32'):
            assert resolve_capture_device_index(pa, "USB Mic") == 0

    def test_system_default_returns_none(self):
        from utils.clAudioDevices import resolve_capture_device_index
        pa = _fake_pa([{"name": "USB Mic", "maxInputChannels": 1}])
        with patch('sys.platform', 'win32'):
            assert resolve_capture_device_index(pa, None) is None
            assert resolve_capture_device_index(pa, "System Default") is None

    def test_non_windows_platforms_use_plain_resolve(self):
        from utils.clAudioDevices import resolve_capture_device_index
        devices = [{"name": "USB Mic", "maxInputChannels": 1, "hostApi": 0}]  # "MME" in HOST_APIS, but platform isn't win32
        pa = _fake_pa(devices, self.HOST_APIS)
        with patch('sys.platform', 'linux'):
            assert resolve_capture_device_index(pa, "USB Mic") == 0


class TestListOutputDeviceNames:
    def test_returns_sdl2_device_names(self):
        with patch('pygame.init'), patch('pygame.quit'), \
             patch('pygame._sdl2.audio.get_audio_device_names', return_value=["Speakers", "Headset"]):
            names = list_output_device_names()
        assert names == ["Speakers", "Headset"]

    def test_enumeration_failure_returns_empty_list(self):
        with patch('pygame.init'), patch('pygame.quit'), \
             patch('pygame._sdl2.audio.get_audio_device_names', side_effect=Exception("no audio subsystem")):
            names = list_output_device_names()
        assert names == []


class TestGetCleanDisplayNames:
    """Windows Core Audio exposes a device's raw hardware name as a
    property separate from the localized 'Category (hardware name)' string
    PortAudio/SDL2 report -- e.g. 'Microphone (G435 ...)' in English and
    'Microfone (G435 ...)' in Portuguese both have the raw name
    'G435 ...'. Only safe to use when unique among the current list --
    onboard multi-jack chips (Realtek/NVIDIA) share one raw name across
    several distinct endpoints, where the localized prefix is what's
    actually distinguishing (Front/Rear/Mic/Line-In, or which monitor)."""

    def _fake_pycaw_device(self, full_name, raw_name):
        dev = MagicMock()
        dev.properties = {
            '{A45C254E-DF1C-4EFD-8020-67D146A850E0} 14': full_name,
            '{026E516E-B814-414B-83CD-856D6FEF4822} 2': raw_name,
        }
        return dev

    def test_unique_raw_name_is_used_as_display(self):
        devices = [self._fake_pycaw_device(
            "Microphone (G435 Wireless Gaming Headset)", "G435 Wireless Gaming Headset"
        )]
        with patch('sys.platform', 'win32'), \
             patch('pycaw.pycaw.AudioUtilities.GetAllDevices', return_value=devices):
            result = get_clean_display_names(["Microphone (G435 Wireless Gaming Headset)"], "input")
        assert result == {"Microphone (G435 Wireless Gaming Headset)": "G435 Wireless Gaming Headset"}

    def test_shared_raw_name_keeps_the_full_localized_name(self):
        devices = [
            self._fake_pycaw_device("Microphone (Realtek High Definition Audio)", "Realtek High Definition Audio"),
            self._fake_pycaw_device("Line In (Realtek High Definition Audio)", "Realtek High Definition Audio"),
        ]
        names = ["Microphone (Realtek High Definition Audio)", "Line In (Realtek High Definition Audio)"]
        with patch('sys.platform', 'win32'), \
             patch('pycaw.pycaw.AudioUtilities.GetAllDevices', return_value=devices):
            result = get_clean_display_names(names, "input")
        assert result == {name: name for name in names}

    def test_system_default_and_unmatched_names_pass_through_unchanged(self):
        with patch('sys.platform', 'win32'), \
             patch('pycaw.pycaw.AudioUtilities.GetAllDevices', return_value=[]):
            result = get_clean_display_names(["System Default"], "input")
        assert result == {"System Default": "System Default"}

    def test_non_windows_returns_names_unchanged(self):
        with patch('sys.platform', 'linux'):
            result = get_clean_display_names(["Microphone (Some Device)"], "input")
        assert result == {"Microphone (Some Device)": "Microphone (Some Device)"}

    def test_pycaw_failure_falls_back_to_names_unchanged(self):
        with patch('sys.platform', 'win32'), \
             patch('pycaw.pycaw.AudioUtilities.GetAllDevices', side_effect=Exception("COM error")):
            result = get_clean_display_names(["Microphone (Some Device)"], "input")
        assert result == {"Microphone (Some Device)": "Microphone (Some Device)"}
