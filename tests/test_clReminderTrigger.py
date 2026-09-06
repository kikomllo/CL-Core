import pytest
import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'utils')))

from clReminderTrigger import get_volume


class TestGetVolumeWindows:
    def test_reads_master_volume_via_pycaw(self, mocker):
        mocker.patch("clReminderTrigger.sys.platform", "win32")
        endpoint_volume = MagicMock()
        endpoint_volume.GetMute.return_value = False
        endpoint_volume.GetMasterVolumeLevelScalar.return_value = 0.65
        fake_audio_utilities = MagicMock()
        fake_audio_utilities.GetSpeakers.return_value.EndpointVolume = endpoint_volume
        fake_pycaw_module = types.ModuleType("pycaw.pycaw")
        fake_pycaw_module.AudioUtilities = fake_audio_utilities
        mocker.patch.dict(sys.modules, {"pycaw": types.ModuleType("pycaw"), "pycaw.pycaw": fake_pycaw_module})

        assert get_volume() == 65

    def test_returns_zero_when_muted(self, mocker):
        mocker.patch("clReminderTrigger.sys.platform", "win32")
        endpoint_volume = MagicMock()
        endpoint_volume.GetMute.return_value = True
        fake_audio_utilities = MagicMock()
        fake_audio_utilities.GetSpeakers.return_value.EndpointVolume = endpoint_volume
        fake_pycaw_module = types.ModuleType("pycaw.pycaw")
        fake_pycaw_module.AudioUtilities = fake_audio_utilities
        mocker.patch.dict(sys.modules, {"pycaw": types.ModuleType("pycaw"), "pycaw.pycaw": fake_pycaw_module})

        assert get_volume() == 0

    def test_falls_back_to_100_when_pycaw_unavailable(self, mocker):
        mocker.patch("clReminderTrigger.sys.platform", "win32")
        mocker.patch.dict(sys.modules, {"pycaw": None, "pycaw.pycaw": None})

        assert get_volume() == 100


class TestGetVolumeLinux:
    def test_reads_amixer_percentage(self, mocker):
        mocker.patch("clReminderTrigger.sys.platform", "linux")
        mocker.patch(
            "clReminderTrigger.subprocess.check_output",
            return_value=b"Simple mixer control 'Master',0\n  Playback channels: Front Left - Front Right\n  Front Left: Playback 45056 [70%] [on]\n"
        )

        assert get_volume() == 70

    def test_returns_zero_when_muted(self, mocker):
        mocker.patch("clReminderTrigger.sys.platform", "linux")
        mocker.patch(
            "clReminderTrigger.subprocess.check_output",
            return_value=b"Front Left: Playback 0 [0%] [off]\n"
        )

        assert get_volume() == 0

    def test_falls_back_to_100_when_amixer_missing(self, mocker):
        mocker.patch("clReminderTrigger.sys.platform", "linux")
        mocker.patch("clReminderTrigger.subprocess.check_output", side_effect=FileNotFoundError("amixer"))

        assert get_volume() == 100
