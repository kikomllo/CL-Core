import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'utils')))

import clAppAudioRouting


class TestBuildWrappedDeviceId:
    """The undocumented SetPersistedDefaultAudioEndpoint call rejects a
    plain Core Audio endpoint id outright -- it must be wrapped into a
    device-interface-path form (see AudioPolicyConfigService.cs in
    EarTrumpet, cross-checked against SoundSwitch's equivalent)."""

    def test_wraps_a_real_endpoint_id_with_the_render_interface_suffix(self):
        wrapped = clAppAudioRouting.build_wrapped_device_id("{0.0.0.00000000}.{04ecd6f3-abc}")

        assert wrapped == r"\\?\SWD#MMDEVAPI#{0.0.0.00000000}.{04ecd6f3-abc}#{e6327cad-dcec-4949-ae8a-991e976a79d2}"

    def test_returns_none_for_empty_string(self):
        assert clAppAudioRouting.build_wrapped_device_id("") is None

    def test_returns_none_for_none(self):
        assert clAppAudioRouting.build_wrapped_device_id(None) is None


class TestMainArgvValidation:
    def test_reports_error_for_wrong_arg_count(self, capsys):
        code = clAppAudioRouting.main(["clAppAudioRouting.py", "123"])

        assert code == 1
        assert "usage" in capsys.readouterr().out.lower()

    def test_reports_error_for_non_integer_pid(self, capsys):
        code = clAppAudioRouting.main(["clAppAudioRouting.py", "not-a-pid", "device-id"])

        assert code == 1
        assert "pid must be an integer" in capsys.readouterr().out

    def test_reports_error_off_windows(self, capsys, mocker):
        mocker.patch("clAppAudioRouting.sys.platform", "linux")

        code = clAppAudioRouting.main(["clAppAudioRouting.py", "123", "device-id"])

        assert code == 1
        assert "windows-only" in capsys.readouterr().out.lower()


class TestMainDispatchesToSetPersistedDefaultAudioEndpoint:
    def test_success_prints_ok_and_returns_zero(self, capsys, mocker):
        mocker.patch("clAppAudioRouting.sys.platform", "win32")
        mock_set = mocker.patch.object(clAppAudioRouting, "set_persisted_default_audio_endpoint")

        code = clAppAudioRouting.main(["clAppAudioRouting.py", "4242", "some-device-id"])

        assert code == 0
        assert capsys.readouterr().out.strip() == "OK"
        mock_set.assert_called_once_with(4242, "some-device-id")

    def test_empty_device_id_still_dispatches_as_a_clear_request(self, capsys, mocker):
        mocker.patch("clAppAudioRouting.sys.platform", "win32")
        mock_set = mocker.patch.object(clAppAudioRouting, "set_persisted_default_audio_endpoint")

        code = clAppAudioRouting.main(["clAppAudioRouting.py", "4242", ""])

        assert code == 0
        mock_set.assert_called_once_with(4242, "")

    def test_a_raised_exception_is_reported_and_returns_one(self, capsys, mocker):
        mocker.patch("clAppAudioRouting.sys.platform", "win32")
        mocker.patch.object(
            clAppAudioRouting, "set_persisted_default_audio_endpoint",
            side_effect=clAppAudioRouting.HResultError("SetPersistedDefaultAudioEndpoint(role=0) failed: HRESULT 0x80070005"),
        )

        code = clAppAudioRouting.main(["clAppAudioRouting.py", "4242", "some-device-id"])

        assert code == 1
        assert "HRESULT 0x80070005" in capsys.readouterr().out
