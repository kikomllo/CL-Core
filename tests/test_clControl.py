import pytest
import os
import sys
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clControl import LightManager, LightCommandError, poll_light_status
from pywizlight import PilotBuilder
from pywizlight.utils import percent_to_hex

@pytest.fixture
def manager():
    with patch.dict(os.environ, {"TAPO_EMAIL": "test@test.com", "TAPO_PASSWORD": "dummy", "LIGHT_TYPE": "tapo"}):
        with patch('utils.clEnvLoader.load_dotenv'):
            from clControl import LightManager
            m = LightManager()
            
    m.last_discovered_devices = [
        {"type": "wiz", "model": "WIZ_BULB", "ip": "192.168.1.88", "mac": "cc40851c1118"},
        {"type": "wiz", "model": "WIZ_BULB", "ip": "192.168.1.217", "mac": "444f8e30aa06"},
        {"type": "tapo", "model": "L530", "ip": "192.168.1.111", "mac": "10:5A:95:B7:0C:86"}
    ]

    m.word_to_number = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4"}
    
    m.update_env_credentials = MagicMock() 
    m.colour_gamma = MagicMock(return_value=1.0)           # the tests must not depend on the correction set in the real core.json
    m._save_devices = MagicMock()
    m._save_last_target = MagicMock()
    
    return m

class TestLightManagerResolution:
    """Tests the fuzzy routing, collision handling, and device resolution of the LightManager."""

    @pytest.mark.parametrize("spoken_text, expected_status, expected_ip, expected_mac, expected_type", [
        # --- 1. DIRECT INDEXING ---
        (2, "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        ("0", "success", "192.168.1.88", "cc40851c1118", "wiz"),
        ("device two please", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        
        # --- 2. SEMANTIC & BRAND TARGETING ---
        ("save the tapo bulb", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        ("connect to the l530", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        
        # --- 3. COLLISIONS & TARGETED RESOLUTION ---
        ("use the wiz bulb", "error", None, None, None), # Collision (2 Wiz bulbs found)
        ("wiz bulb 88", "success", "192.168.1.88", "cc40851c1118", "wiz"), # Resolves collision
        ("192.168.1.217", "success", "192.168.1.217", "444f8e30aa06", "wiz"), # Exact IP match
        
        # --- 4. PRECEDENCE & CHAOS FORMATTING ---
        ("tapo bulb 0", "success", "192.168.1.88", "cc40851c1118", "wiz"), # Numeric index beats brand
        ("one hundred", "success", "192.168.1.217", "444f8e30aa06", "wiz"), # First valid number (1) wins
        ("   TaPo   BuLb  ", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        
        # --- 5. OUT OF BOUNDS & GARBAGE INPUT ---
        (1030, "error", None, None, None),
        ("device four", "error", None, None, None),
        ("save the philips hue", "error", None, None, None),
        ("   ", "error", None, None, None),
    ])
    
    def test_device_routing(self, manager, spoken_text, expected_status, expected_ip, expected_mac, expected_type):
        manager.update_env_credentials.reset_mock()
        result = manager._handle_discovery_selection(spoken_text)
        assert result["status"] == expected_status, f"Status mismatch for '{spoken_text}'"
        if expected_status == "success":
            manager.update_env_credentials.assert_called_with(expected_ip, expected_mac, expected_type, "main")

    def test_octet_collision_prevention(self, manager):
        """Ensures '11' targets .11 and not .111 when evaluating octets."""
        manager.last_discovered_devices = [
            {"type": "tapo", "model": "L530", "ip": "192.168.1.11", "mac": "AA"},
            {"type": "tapo", "model": "L530", "ip": "192.168.1.111", "mac": "BB"}
        ]
        
        result = manager._handle_discovery_selection("tapo 11")
        
        assert result["status"] == "success", "Failed to resolve device cleanly."
        manager.update_env_credentials.assert_called_with("192.168.1.11", "AA", "tapo", "main")

    def test_empty_memory_catch(self, manager):
        """Ensures the system rejects selection attempts if no scan was performed."""
        manager.last_discovered_devices = []
        
        result = manager._handle_discovery_selection(1)
        
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_last_target_memory(self, manager, mocker):
        """Ensures generic light commands target the last modified light instead of all."""
        manager.lights = {
            "bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"},
            "desk_light": {"ip": "192.168.1.11", "mac": "BB", "type": "wiz"}
        }
        mocker.patch.object(manager, '_execute_wiz_target')
        
        # 1. Reset last_target to 'all'
        manager.last_target = "all"
        assert manager.last_target == "all"

        # 2. Control bedroom light explicitly
        await manager.control_bulb(off=True, target_name="bedroom light")
        assert manager.last_target == "bedroom"

        # 3. Generic turn off the light should target bedroom, not all
        await manager.control_bulb(off=True, target_name="the light")
        assert manager.last_target == "bedroom"

        # 4. Explicit turn off all lights sets last_target to all
        await manager.control_bulb(off=True, target_name="all lights")
        assert manager.last_target == "all"

class TestRefreshLightsPicksUpNetworkChanges:
    """The dashboard's lights-widget refresh button dispatches
    refresh_lights (see mqtt_service_listener), which used to just set
    poll_trigger -- re-polling the on/off state of whichever lights were
    loaded at process startup. Switching Wi-Fi networks and hitting
    refresh still showed the old network's lights, since manager.lights
    was never reloaded. refresh_lights() must call _load_devices() again
    so a network change is picked up too."""

    def _write_devices_file(self, tmp_path):
        devices_file = tmp_path / "devices.json"
        devices_file.write_text(json.dumps({
            "networks": {
                "HomeWiFi": {"lamp": {"ip": "192.168.1.10", "type": "wiz"}},
                "OfficeWiFi": {"desk_light": {"ip": "192.168.1.20", "type": "wiz"}},
            }
        }))
        return devices_file

    def test_refreshing_after_a_network_change_reloads_the_new_networks_lights(self, manager, mocker, tmp_path):
        manager.devices_file = str(self._write_devices_file(tmp_path))
        mocker.patch("clControl.get_current_wifi_ssid", return_value="HomeWiFi")
        manager.lights = manager._load_devices()
        assert manager.lights == {"lamp": {"ip": "192.168.1.10", "type": "wiz"}}

        mocker.patch("clControl.get_current_wifi_ssid", return_value="OfficeWiFi")
        manager.refresh_lights()

        assert manager.lights == {"desk_light": {"ip": "192.168.1.20", "type": "wiz"}}
        assert manager.current_ssid == "OfficeWiFi"

    def test_refresh_lights_still_wakes_the_polling_loop(self, manager, mocker, tmp_path):
        manager.devices_file = str(self._write_devices_file(tmp_path))
        mocker.patch("clControl.get_current_wifi_ssid", return_value="HomeWiFi")

        manager.refresh_lights()

        assert manager.poll_trigger.is_set()


class TestControlEdgeCases:
    @pytest.mark.asyncio
    async def test_network_timeout(self, manager, mocker):
        """Ensure network timeouts during light operations are gracefully handled."""
        manager.lights = {
            "bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}
        }
        manager.last_target = "bedroom"
        
        # Mock wizlight.updateState to raise TimeoutError
        import asyncio
        mock_wiz = mocker.patch('clControl.wizlight')
        mock_wiz.return_value.updateState = mocker.AsyncMock(side_effect=asyncio.TimeoutError("Bulb offline"))
        
        # A light that can't be reached is reported, not silently treated as done
        with pytest.raises(LightCommandError, match="couldn't reach the bedroom"):
            await manager.control_bulb(on=True, target_name="bedroom")

    @pytest.mark.asyncio
    async def test_invalid_color_hex(self, manager, mocker):
        """Ensure invalid color strings do not crash the control loop."""
        manager.lights = {
            "bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}
        }
        manager.last_target = "bedroom"
        
        # Mock the hardware call so it doesn't try to connect to the real IP and hang
        mock_wiz = mocker.patch('clControl.wizlight')
        mock_wiz.return_value.turn_on = mocker.AsyncMock()
        
        # Provide non-hex color string
        await manager.control_bulb(color="not_a_hex_color", target_name="bedroom")

        # Execution shouldn't crash.
        assert True

class TestBrightness:
    @pytest.mark.asyncio
    async def test_wiz_absolute_lum_converts_percent_correctly(self, manager, mocker):
        """lum is a 0-100 percent, but PilotBuilder.brightness expects a 0-255
        value it converts back to percent internally -- passing lum straight
        through used to land at roughly 40% of the requested level."""
        mock_wiz = mocker.patch('clControl.wizlight')
        mock_wiz.return_value.turn_on = mocker.AsyncMock()

        await manager._execute_wiz_target("192.168.1.10", False, True, False, None, 60, None)

        mock_wiz.return_value.turn_on.assert_called_once()
        pilot_builder = mock_wiz.return_value.turn_on.call_args[0][0]
        assert pilot_builder.pilot_params["dimming"] == 60

    @pytest.mark.asyncio
    async def test_adjust_brightness_down_steps_from_current_level(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}}
        manager.last_target = "bedroom"

        mock_wiz = mocker.patch('clControl.wizlight')
        mock_state = MagicMock()
        mock_state.get_brightness.return_value = percent_to_hex(50)  # currently at 50%
        mock_wiz.return_value.updateState = mocker.AsyncMock()
        mock_wiz.return_value.state = [mock_state]

        mock_execute = mocker.patch.object(manager, '_execute_wiz_target', new=mocker.AsyncMock())
        await manager.adjust_brightness("down", target_name="bedroom")

        mock_execute.assert_called_once_with("192.168.1.10", False, True, False, None, 30, None)

    @pytest.mark.asyncio
    async def test_adjust_brightness_up_clamps_at_100(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}}
        manager.last_target = "bedroom"

        mock_wiz = mocker.patch('clControl.wizlight')
        mock_state = MagicMock()
        mock_state.get_brightness.return_value = percent_to_hex(90)
        mock_wiz.return_value.updateState = mocker.AsyncMock()
        mock_wiz.return_value.state = [mock_state]

        mock_execute = mocker.patch.object(manager, '_execute_wiz_target', new=mocker.AsyncMock())
        await manager.adjust_brightness("up", target_name="bedroom")

        mock_execute.assert_called_once_with("192.168.1.10", False, True, False, None, 100, None)

    @pytest.mark.asyncio
    async def test_adjust_brightness_down_clamps_at_1(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}}
        manager.last_target = "bedroom"

        mock_wiz = mocker.patch('clControl.wizlight')
        mock_state = MagicMock()
        mock_state.get_brightness.return_value = percent_to_hex(10)
        mock_wiz.return_value.updateState = mocker.AsyncMock()
        mock_wiz.return_value.state = [mock_state]

        mock_execute = mocker.patch.object(manager, '_execute_wiz_target', new=mocker.AsyncMock())
        await manager.adjust_brightness("down", target_name="bedroom")

        mock_execute.assert_called_once_with("192.168.1.10", False, True, False, None, 1, None)

class TestFailureReporting:
    """A command a light refuses or can't receive must surface as an error, not as success."""

    @pytest.fixture
    def two_lights(self, manager):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"},
                          "kitchen": {"ip": "192.168.1.11", "mac": "BB", "type": "wiz"}}
        return manager

    @pytest.mark.asyncio
    async def test_one_failing_light_does_not_stop_the_others_but_is_reported(self, two_lights, mocker):
        reached = []

        async def fake_wiz(ip, *args):
            if ip.endswith(".10"):
                raise TimeoutError("offline")
            reached.append(ip)
        mocker.patch.object(two_lights, "_execute_wiz_target", side_effect=fake_wiz)
        with pytest.raises(LightCommandError) as err:
            await two_lights.control_bulb(off=True, target_name="all")
        assert reached == ["192.168.1.11"]
        assert "bedroom" in str(err.value) and "kitchen" not in str(err.value)

    @pytest.mark.asyncio
    async def test_a_tapo_login_refusal_names_the_fix(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "tapo"}}
        mocker.patch.object(manager, "_execute_tapo_target", side_effect=Exception(
            'Tapo(Unauthorized { kind: "FORBIDDEN", description: "Make sure Third-Party Compatibility is turned on" })'))
        with pytest.raises(LightCommandError, match="Third-Party Compatibility"):
            await manager.control_bulb(off=True, target_name="bedroom")

    @pytest.mark.asyncio
    async def test_success_does_not_raise(self, two_lights, mocker):
        mocker.patch.object(two_lights, "_execute_wiz_target", new=mocker.AsyncMock())
        await two_lights.control_bulb(on=True, target_name="all")

    @pytest.mark.asyncio
    async def test_brightness_changes_report_failures_too(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}}
        mock_wiz = mocker.patch('clControl.wizlight')
        mock_wiz.return_value.updateState = mocker.AsyncMock(side_effect=TimeoutError("offline"))
        with pytest.raises(LightCommandError):
            await manager.adjust_brightness("up", target_name="bedroom")


class TestExplicitRgb:
    """The dashboard colour picker sends an explicit rgb next to (or instead of) a colour name."""

    @pytest.mark.parametrize("value, expected", [
        (None, None), ([255, 0, 0], (255, 0, 0)), ((10, 20, 30), (10, 20, 30)), ([0.4, 254.6, 128], (0, 255, 128)),
    ])
    def test_valid_values_are_normalised(self, value, expected):
        from clControl import _parse_rgb
        assert _parse_rgb(value) == expected

    @pytest.mark.parametrize("value", [[255, 0], [1, 2, 3, 4], "red", 5, [256, 0, 0], [-1, 0, 0], ["a", 0, 0], [True, 0, 0], [None, 0, 0]])
    def test_bad_values_are_refused(self, value):
        from clControl import _parse_rgb
        with pytest.raises(ValueError):
            _parse_rgb(value)

    @pytest.mark.asyncio
    async def test_a_bad_rgb_is_refused_before_any_light_is_touched(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}}
        execute = mocker.patch.object(manager, "_execute_wiz_target", new=mocker.AsyncMock())
        with pytest.raises(ValueError):
            await manager.control_bulb(on=True, rgb=[999, 0, 0], target_name="bedroom")
        execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_rgb_reaches_a_wiz_light_as_a_keyword_and_old_calls_keep_their_shape(self, manager, mocker):
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"}}
        execute = mocker.patch.object(manager, "_execute_wiz_target", new=mocker.AsyncMock())
        await manager.control_bulb(on=True, rgb=[10, 20, 30], lum=40, target_name="bedroom")
        execute.assert_called_once_with("192.168.1.10", False, True, False, None, 40, None, rgb=(10, 20, 30))
        execute.reset_mock()
        await manager.control_bulb(on=True, lum=40, target_name="bedroom")
        execute.assert_called_once_with("192.168.1.10", False, True, False, None, 40, None)

    @pytest.mark.asyncio
    async def test_wiz_turns_on_in_the_given_colour_at_the_given_brightness(self, manager, mocker):
        mock_wiz = mocker.patch("clControl.wizlight")
        mock_wiz.return_value.turn_on = mocker.AsyncMock()
        await manager._execute_wiz_target("192.168.1.10", False, True, False, None, 60, None, rgb=(150, 60, 220))
        params = mock_wiz.return_value.turn_on.call_args[0][0].pilot_params
        # pywizlight rescales an rgb inside PilotBuilder, so compare with what the library makes of the same input
        assert params == PilotBuilder(brightness=percent_to_hex(60), rgb=(150, 60, 220)).pilot_params
        assert params["dimming"] == 60

    @pytest.mark.asyncio
    async def test_an_explicit_rgb_beats_a_colour_name(self, manager, mocker):
        mock_wiz = mocker.patch("clControl.wizlight")
        mock_wiz.return_value.turn_on = mocker.AsyncMock()
        await manager._execute_wiz_target("192.168.1.10", False, True, False, "red", None, None, rgb=(0, 0, 255))
        params = mock_wiz.return_value.turn_on.call_args[0][0].pilot_params
        assert params == PilotBuilder(brightness=255, rgb=(0, 0, 255)).pilot_params

    @pytest.mark.asyncio
    async def test_a_named_colour_still_works_without_rgb(self, manager, mocker):
        mock_wiz = mocker.patch("clControl.wizlight")
        mock_wiz.return_value.turn_on = mocker.AsyncMock()
        manager.color_matrix = {"red": {"type": "rgb", "r": 255, "g": 0, "b": 0}}
        await manager._execute_wiz_target("192.168.1.10", False, True, False, "red", None, None)
        params = mock_wiz.return_value.turn_on.call_args[0][0].pilot_params
        assert params == PilotBuilder(brightness=255, rgb=(255, 0, 0)).pilot_params

    def _tapo(self, manager, mocker):
        device = MagicMock()
        for name in ("on", "off", "set_brightness", "set_hue_saturation", "set_color_temperature"):
            setattr(device, name, mocker.AsyncMock())
        manager.tapo_client = MagicMock()
        manager.tapo_client.l530 = mocker.AsyncMock(return_value=device)
        return device

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rgb, hue, sat", [((255, 0, 0), 0, 100), ((0, 0, 255), 240, 100), ((255, 255, 255), 0, 1)])          # a white is 1 %, not 0: the bulb refuses a saturation of 0
    async def test_tapo_gets_hue_and_saturation_for_the_colour(self, manager, mocker, rgb, hue, sat):
        device = self._tapo(manager, mocker)
        await manager._execute_tapo_target("192.168.1.10", False, True, False, None, 50, None, rgb=rgb)
        device.on.assert_called_once()
        device.set_hue_saturation.assert_called_once_with(hue, sat)
        device.set_brightness.assert_called_once_with(50)

    @pytest.mark.asyncio
    async def test_tapo_rgb_beats_a_colour_name(self, manager, mocker):
        device = self._tapo(manager, mocker)
        manager.color_matrix = {"red": {"type": "rgb", "r": 255, "g": 0, "b": 0}}
        await manager._execute_tapo_target("192.168.1.10", False, True, False, "red", None, None, rgb=(0, 0, 255))
        device.set_hue_saturation.assert_called_once_with(240, 100)


class TestLightStatusColourFields:
    """The status the dashboard shows the colour button from: brightness, the colour or the white temperature, and
    whether the light has colour at all."""

    def _wiz(self, mocker, rgb=(None, None, None), temp=None, brightness=None, color_feature=True):
        state = MagicMock()
        state.get_rgb.return_value = rgb
        state.get_colortemp.return_value = temp
        state.get_brightness.return_value = brightness
        bulb = MagicMock()
        bulb.state = [state]
        bulb.status = True
        bulb.updateState = mocker.AsyncMock()
        bulb.bulbtype.features.color = color_feature
        return bulb

    def test_a_wiz_bulb_in_colour_mode_reports_its_rgb(self, mocker):
        from clControl import _wiz_status_fields
        out = _wiz_status_fields(self._wiz(mocker, rgb=(150, 60, 220), brightness=percent_to_hex(60)))
        assert out == {"brightness": 60, "rgb": [150, 60, 220], "kelvin": None}

    def test_a_wiz_bulb_in_white_mode_reports_kelvin(self, mocker):
        from clControl import _wiz_status_fields
        out = _wiz_status_fields(self._wiz(mocker, temp=2700, brightness=percent_to_hex(100)))
        assert out == {"brightness": 100, "rgb": None, "kelvin": 2700}

    def test_a_wiz_bulb_with_no_state_reports_nothing(self, mocker):
        from clControl import _wiz_status_fields
        bulb = self._wiz(mocker)
        bulb.state = None
        assert _wiz_status_fields(bulb) == {"brightness": None, "rgb": None, "kelvin": None}

    def test_a_tapo_bulb_in_colour_mode_reports_rgb_from_hue_and_saturation(self):
        from clControl import _tapo_status_fields
        info = MagicMock(brightness=70, color_temp=0, hue=240, saturation=100)
        assert _tapo_status_fields(info) == {"brightness": 70, "rgb": [0, 0, 255], "kelvin": None}

    def test_a_tapo_bulb_in_white_mode_reports_kelvin(self):
        from clControl import _tapo_status_fields
        info = MagicMock(brightness=100, color_temp=4000, hue=0, saturation=0)
        assert _tapo_status_fields(info) == {"brightness": 100, "rgb": None, "kelvin": 4000}

    @pytest.mark.parametrize("entry, detected, expected", [
        ({}, None, True), ({}, True, True), ({}, False, False),
        ({"colour": False}, True, False), ({"colour": True}, False, True), ({"colour": "no"}, False, False),
    ])
    def test_colour_capability(self, entry, detected, expected):
        from clControl import _colour_capability
        assert _colour_capability(entry, detected) is expected

    @pytest.mark.asyncio
    async def test_the_polled_status_carries_brand_capability_and_colour(self, manager, mocker, mock_mqtt):
        import asyncio
        manager.lights = {"bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"},
                          "kitchen": {"ip": "192.168.1.11", "mac": "BB", "type": "wiz", "colour": False}}
        manager.current_ssid = "TestNet"
        mock_wiz = mocker.patch("clControl.wizlight")
        mock_wiz.return_value = self._wiz(mocker, rgb=(150, 60, 220), brightness=percent_to_hex(60))
        task = asyncio.create_task(poll_light_status(manager))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if mock_mqtt.publish.await_count:
                break
        task.cancel()
        await task                                   # the loop swallows the cancellation and returns
        topic, body = mock_mqtt.publish.await_args_list[0].args
        assert topic == "jarvis/sys/light_status"
        lights = {l["name"]: l for l in json.loads(body)["lights"]}
        assert lights["Bedroom"]["brand"] == "wiz" and lights["Bedroom"]["colour"] is True
        assert lights["Bedroom"]["rgb"] == [150, 60, 220] and lights["Bedroom"]["brightness"] == 60
        assert lights["Kitchen"]["colour"] is False                      # devices.json override


class TestTapoSessionHandling:
    """A bulb takes one session at a time and the Tapo library ignores cancellation: calls to one bulb are serialised, bounded and
    reuse their login (live log 2026-11-24: overlapping toggle/poll gave SESSION_TIMEOUT and Handshake2 failed, a dead bulb stalled for ~20 s)."""

    def _device(self, mocker, info=None):
        device = MagicMock()
        device.get_device_info = mocker.AsyncMock(return_value=info or MagicMock(device_on=True, brightness=50, color_temp=3000))
        for name in ("on", "off"):
            setattr(device, name, mocker.AsyncMock())
        return device

    def _client(self, manager, mocker, device):
        manager.tapo_client = MagicMock()
        manager.tapo_client.l530 = mocker.AsyncMock(return_value=device)

    @pytest.mark.asyncio
    async def test_bounded_returns_on_time_even_when_the_call_ignores_cancellation(self):
        import asyncio, time
        from clControl import _bounded

        async def stubborn():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.4)             # a Rust call that finishes its own timeout first
                raise RuntimeError("late failure nobody awaits")
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await _bounded(stubborn(), 0.05)
        assert time.monotonic() - start < 0.3

    @pytest.mark.asyncio
    async def test_bounded_passes_the_result_and_the_error_through(self):
        from clControl import _bounded

        async def ok(): return 7
        async def bad(): raise ValueError("x")
        assert await _bounded(ok(), 1) == 7
        with pytest.raises(ValueError):
            await _bounded(bad(), 1)

    @pytest.mark.asyncio
    async def test_calls_to_one_bulb_never_overlap_but_different_bulbs_do(self, manager, mocker):
        import asyncio
        self._client(manager, mocker, self._device(mocker))
        running, peak = {}, {}

        def op_for(ip):
            async def op(device):
                running[ip] = running.get(ip, 0) + 1
                peak[ip] = max(peak.get(ip, 0), running[ip])
                await asyncio.sleep(0.02)
                running[ip] -= 1
            return op
        await asyncio.gather(*(manager._tapo_call("10.0.0.1", op_for("10.0.0.1")) for _ in range(4)),
                             *(manager._tapo_call("10.0.0.2", op_for("10.0.0.2")) for _ in range(2)))
        assert peak == {"10.0.0.1": 1, "10.0.0.2": 1}

    @pytest.mark.asyncio
    async def test_the_login_is_reused_between_calls(self, manager, mocker):
        device = self._device(mocker)
        self._client(manager, mocker, device)
        await manager._tapo_call("10.0.0.1", lambda d: d.get_device_info())
        await manager._tapo_call("10.0.0.1", lambda d: d.get_device_info())
        manager.tapo_client.l530.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_expired_session_is_retried_once_with_a_fresh_login(self, manager, mocker):
        device = self._device(mocker)
        device.off.side_effect = [Exception('Tapo(Unauthorized { kind: "SESSION_TIMEOUT" })'), None]
        self._client(manager, mocker, device)
        await manager._execute_tapo_target("10.0.0.1", False, False, True, None, None, None)
        assert device.off.await_count == 2 and manager.tapo_client.l530.await_count == 2

    @pytest.mark.asyncio
    async def test_other_errors_and_timeouts_are_not_retried_and_drop_the_handle(self, manager, mocker):
        import asyncio
        device = self._device(mocker)
        device.on.side_effect = Exception("connection refused")
        self._client(manager, mocker, device)
        with pytest.raises(Exception, match="refused"):
            await manager._tapo_call("10.0.0.1", lambda d: d.on())
        assert device.on.await_count == 1 and "10.0.0.1" not in manager._tapo_devices

        async def silent(d): await asyncio.sleep(10)
        with pytest.raises(asyncio.TimeoutError):
            await manager._tapo_call("10.0.0.1", silent, timeout=0.05)
        assert "10.0.0.1" not in manager._tapo_devices

    @pytest.mark.asyncio
    async def test_tapo_brightness_is_read_and_written_in_one_session(self, manager, mocker):
        device = self._device(mocker, MagicMock(device_on=True, brightness=50, color_temp=3000))
        device.set_brightness = mocker.AsyncMock()
        self._client(manager, mocker, device)
        manager.lights = {"bedroom": {"ip": "10.0.0.1", "mac": "AA", "type": "tapo"}}
        await manager.adjust_brightness("up", target_name="bedroom")
        device.set_brightness.assert_awaited_once_with(70)
        manager.tapo_client.l530.assert_awaited_once()


class TestPollingResilience:
    async def _run_poll(self, manager, mock_mqtt, publishes, mocker, interval=None):
        import asyncio
        if interval is not None:
            mocker.patch("clControl.POLL_INTERVAL", interval)
        task = asyncio.create_task(poll_light_status(manager))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if mock_mqtt.publish.await_count >= publishes:
                break
        task.cancel()
        await task
        return [json.loads(c.args[1])["lights"][0] for c in mock_mqtt.publish.await_args_list]

    @pytest.mark.asyncio
    async def test_a_failed_poll_is_unknown_not_off_until_it_is_offline(self, manager, mocker, mock_mqtt):
        manager.lights = {"bedroom": {"ip": "10.0.0.1", "mac": "AA", "type": "tapo"}}
        manager.current_ssid = "TestNet"
        mocker.patch.object(manager, "_tapo_call", side_effect=TimeoutError("no answer"))
        statuses = await self._run_poll(manager, mock_mqtt, 3, mocker, interval=0.01)
        assert [(s["is_on"], s["offline"]) for s in statuses[:3]] == [(None, False), (None, False), (False, True)]

    @pytest.mark.asyncio
    async def test_the_lights_are_polled_again_without_anyone_asking(self, manager, mocker, mock_mqtt):
        manager.lights = {"bedroom": {"ip": "10.0.0.1", "mac": "AA", "type": "tapo"}}
        manager.current_ssid = "TestNet"
        mocker.patch.object(manager, "_tapo_call", new=mocker.AsyncMock(return_value=MagicMock(device_on=True, brightness=50, color_temp=3000)))
        statuses = await self._run_poll(manager, mock_mqtt, 2, mocker, interval=0.02)
        assert len(statuses) >= 2 and all(s["is_on"] is True for s in statuses)


class TestFeedbackDelivery:
    @pytest.mark.asyncio
    async def test_a_dead_connection_falls_back_to_a_fresh_one(self, mocker, mock_mqtt):
        from clControl import _publish_feedback
        dead = mocker.AsyncMock()
        dead.publish.side_effect = Exception("Could not publish message")
        await _publish_feedback(dead, {"status": "error"})
        mock_mqtt.publish.assert_awaited_once()
        assert json.loads(mock_mqtt.publish.await_args.args[1]) == {"status": "error"}

    @pytest.mark.asyncio
    async def test_a_failure_to_report_never_raises(self, mocker, mock_mqtt):
        from clControl import _publish_feedback
        dead = mocker.AsyncMock()
        dead.publish.side_effect = Exception("down")
        mock_mqtt.publish.side_effect = Exception("still down")
        await _publish_feedback(dead, {"status": "error"})

class TestTuyaPlug:
    """The LSC heater plug: a `tuya` device of kind `plug`. On/off only, never part of "all lights", the key from .env."""

    HEATER = {"ip": "10.0.0.60", "type": "tuya", "kind": "plug", "device_id": "bf1", "version": "3.5"}

    @pytest.fixture
    def home(self, manager, mocker, monkeypatch):
        manager.lights = {
            "bedroom": {"ip": "10.0.0.1", "mac": "AA", "type": "wiz"},
            "heater": dict(self.HEATER),
        }
        manager.current_ssid = "TestNet"
        monkeypatch.setenv("TUYA_KEY_HEATER", "the-key")
        manager.tuya = {"set": mocker.patch("clControl.clTuya.set_power", new=mocker.AsyncMock()),
                        "toggle": mocker.patch("clControl.clTuya.toggle_power", new=mocker.AsyncMock(return_value=True)),
                        "get": mocker.patch("clControl.clTuya.get_power", new=mocker.AsyncMock(return_value=True))}
        manager.wiz = mocker.patch("clControl.wizlight")
        manager.wiz.return_value.turn_on = mocker.AsyncMock()
        manager.wiz.return_value.turn_off = mocker.AsyncMock()
        return manager

    def test_all_lights_leaves_the_plug_out_but_its_name_finds_it(self, home):
        for spoken in ("all", "all lights", "everything", "every light"):
            assert [t[0] for t in home._resolve_targets(spoken)] == ["bedroom"], spoken
        assert [t[0] for t in home._resolve_targets("the heater")] == ["heater"]
        assert [t[0] for t in home._resolve_targets("heater")] == ["heater"]

    def test_a_device_without_kind_is_a_light(self, home):
        assert [t[0] for t in home._resolve_targets("bedroom")] == ["bedroom"]
        assert "kind" not in home.lights["bedroom"]

    def test_the_light_after_the_heater_is_not_the_heater_but_it_is(self, home):
        home.last_target = "heater"
        assert [t[0] for t in home._resolve_targets("the light")] == ["bedroom"]
        home.last_target = "heater"
        assert [t[0] for t in home._resolve_targets("")] == ["heater"]          # "turn it off" after "turn on the heater"

    def test_light_color_is_no_target_so_the_last_light_applies(self, home):
        """"change the light color to warm" captures "light color" as the target."""
        home.last_target = "bedroom"
        assert [t[0] for t in home._resolve_targets("light color")] == ["bedroom"]
        home.last_target = "heater"
        assert [t[0] for t in home._resolve_targets("the light colour")] == ["bedroom"]   # never the plug

    @pytest.mark.asyncio
    async def test_on_and_off_reach_the_plug_with_its_key(self, home):
        await home.control_bulb(on=True, target_name="heater")
        await home.control_bulb(off=True, target_name="the heater")
        calls = home.tuya["set"].await_args_list
        assert [c.args[2] for c in calls] == [True, False]
        assert calls[0].args[0]["device_id"] == "bf1" and calls[0].args[1] == "the-key"

    @pytest.mark.asyncio
    async def test_toggle_goes_through_the_one_session_toggle(self, home):
        await home.control_bulb(toggle=True, target_name="heater")
        home.tuya["toggle"].assert_awaited_once()
        home.tuya["set"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_lights_off_never_touches_the_heater(self, home):
        await home.control_bulb(off=True, target_name="all lights")
        home.tuya["set"].assert_not_awaited()
        home.wiz.return_value.turn_off.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [{"color": "red"}, {"lum": 50}, {"temp": 30}, {"rgb": [1, 2, 3]}])
    async def test_colour_or_brightness_for_a_plug_is_a_clear_error_and_nothing_happens(self, home, extra):
        with pytest.raises(LightCommandError, match="heater is a plug"):
            await home.control_bulb(on=True, target_name="heater", **extra)
        home.tuya["set"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_brightness_steps_for_a_plug_are_refused(self, home):
        with pytest.raises(LightCommandError, match="no brightness"):
            await home.adjust_brightness("up", "heater")

    @pytest.mark.asyncio
    async def test_a_plug_that_fails_is_reported_with_the_reason(self, home):
        from utils.clTuya import TuyaError
        home.tuya["set"].side_effect = TuyaError("Check device key or version (when switching the plug): check the local key.")
        with pytest.raises(LightCommandError, match="couldn't reach the heater.*local key"):
            await home.control_bulb(on=True, target_name="heater")

    def test_the_key_comes_from_the_environment_never_from_devices_json(self, home, monkeypatch):
        assert home.tuya_key("heater") == "the-key"
        monkeypatch.delenv("TUYA_KEY_HEATER")
        assert home.tuya_key("heater") == ""
        monkeypatch.setenv("TUYA_KEY_LIVING_ROOM_HEATER", "k2")
        assert home.tuya_key("living room heater") == "k2"
        tracked = open(os.path.join(os.path.dirname(__file__), "..", "config", "devices.json"), encoding="utf-8").read()
        assert "local_key" not in tracked and "TUYA_KEY" not in tracked


class TestTuyaPlugStatus:
    HEATER = TestTuyaPlug.HEATER

    async def _statuses(self, manager, mock_mqtt, mocker, publishes, interval=0.02):
        import asyncio
        mocker.patch("clControl.POLL_INTERVAL", interval)
        task = asyncio.create_task(poll_light_status(manager))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if mock_mqtt.publish.await_count >= publishes:
                break
        task.cancel()
        await task
        return [{l["name"]: l for l in json.loads(c.args[1])["lights"]} for c in mock_mqtt.publish.await_args_list]

    @pytest.mark.asyncio
    async def test_a_plug_reports_on_off_as_a_plug_without_colour(self, manager, mocker, mock_mqtt):
        manager.lights = {"heater": dict(self.HEATER)}
        manager.current_ssid = "TestNet"
        mocker.patch("clControl.clTuya.get_power", new=mocker.AsyncMock(return_value=True))
        first = (await self._statuses(manager, mock_mqtt, mocker, 1))[0]["Heater"]
        assert (first["is_on"], first["offline"], first["kind"], first["brand"], first["colour"]) == (True, False, "plug", "tuya", False)
        assert first["brightness"] is None and first["rgb"] is None

    @pytest.mark.asyncio
    async def test_a_light_reports_kind_light(self, manager, mocker, mock_mqtt):
        manager.lights = {"bedroom": {"ip": "10.0.0.1", "mac": "AA", "type": "tapo"}}
        manager.current_ssid = "TestNet"
        mocker.patch.object(manager, "_tapo_call", new=mocker.AsyncMock(return_value=MagicMock(device_on=True, brightness=50, color_temp=3000)))
        assert (await self._statuses(manager, mock_mqtt, mocker, 1))[0]["Bedroom"]["kind"] == "light"

    @pytest.mark.asyncio
    async def test_an_unreachable_plug_is_unknown_then_offline_and_never_gets_a_colour_button(self, manager, mocker, mock_mqtt):
        manager.lights = {"heater": dict(self.HEATER)}
        manager.current_ssid = "TestNet"
        mocker.patch("clControl.clTuya.get_power", new=mocker.AsyncMock(side_effect=TimeoutError("no answer")))
        mocker.patch("clControl.PLUG_POLL_INTERVAL", 0)
        statuses = [s["Heater"] for s in await self._statuses(manager, mock_mqtt, mocker, 3)]
        assert [(s["is_on"], s["offline"], s["colour"]) for s in statuses[:3]] == [(None, False, False), (None, False, False), (False, True, False)]

    @pytest.mark.asyncio
    async def test_a_plug_is_read_less_often_than_the_bulbs_unless_someone_asks(self, manager, mocker, mock_mqtt):
        manager.lights = {"heater": dict(self.HEATER)}
        manager.current_ssid = "TestNet"
        get = mocker.patch("clControl.clTuya.get_power", new=mocker.AsyncMock(return_value=False))
        await self._statuses(manager, mock_mqtt, mocker, 5)           # five publishes, 20 ms apart: far inside the 45 s plug interval
        assert get.await_count == 1


class TestTapoHueSaturation:
    """What a Tapo bulb accepts: hue 0-360 and saturation 1-100 (a saturation of 0 is refused)."""

    @pytest.mark.parametrize("rgb, expected", [
        ((255, 0, 0), (0, 100)), ((0, 255, 0), (120, 100)), ((0, 0, 255), (240, 100)), ((128, 0, 128), (300, 100)),
        ((116, 10, 255), (266, 96)), ((255, 105, 180), (330, 59)),
    ])
    def test_a_colour_converts_to_hue_and_saturation(self, rgb, expected):
        from clControl import _tapo_hue_saturation
        assert _tapo_hue_saturation(*rgb) == expected

    @pytest.mark.parametrize("rgb", [(255, 255, 255), (0, 0, 0), (128, 128, 128), (254, 255, 255)])
    def test_a_white_or_a_grey_never_gives_saturation_zero(self, rgb):
        from clControl import _tapo_hue_saturation
        hue, sat = _tapo_hue_saturation(*rgb)
        assert 0 <= hue <= 360 and 1 <= sat <= 100

    def test_every_colour_the_wheel_can_make_is_in_range(self):
        import colorsys
        from clControl import _tapo_hue_saturation
        for hue in range(0, 360, 7):
            for sat in (0, 0.01, 0.2, 0.5, 0.99, 1):
                rgb = tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(hue / 360, sat, 1))
                h, s = _tapo_hue_saturation(*rgb)
                assert 0 <= h <= 360 and 1 <= s <= 100, (rgb, h, s)

    @pytest.mark.asyncio
    async def test_a_white_sent_to_a_tapo_bulb_reaches_it_with_a_valid_saturation(self, manager, mocker):
        device = mocker.MagicMock()
        device.on = mocker.AsyncMock()
        device.set_brightness = mocker.AsyncMock()
        device.set_hue_saturation = mocker.AsyncMock()
        await manager._apply_tapo(device, False, True, False, None, 60, None, rgb=(255, 255, 255))
        device.set_hue_saturation.assert_awaited_once()
        hue, sat = device.set_hue_saturation.await_args.args
        assert 1 <= sat <= 100


class TestColourGamma:
    """The correction between a screen's colours and a Tapo bulb's light: 1 sends the colour as it is, more deepens the mid tones."""

    def test_gamma_one_changes_nothing(self):
        from clControl import _tapo_hue_saturation
        assert _tapo_hue_saturation(116, 10, 255, 1.0) == _tapo_hue_saturation(116, 10, 255) == (266, 96)

    def test_a_higher_gamma_deepens_the_colour_and_moves_a_violet_towards_blue(self):
        from clControl import _tapo_hue_saturation
        plain_h, plain_s = _tapo_hue_saturation(116, 10, 255, 1.0)
        h, s = _tapo_hue_saturation(116, 10, 255, 2.2)
        assert (h, s) == (251, 100) and h < plain_h and s > plain_s

    @pytest.mark.parametrize("rgb", [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    def test_the_pure_colours_do_not_move(self, rgb):
        from clControl import _tapo_hue_saturation
        assert _tapo_hue_saturation(*rgb, gamma=2.2) == _tapo_hue_saturation(*rgb, gamma=1.0)

    def test_a_white_is_still_a_valid_saturation(self):
        from clControl import _tapo_hue_saturation
        assert _tapo_hue_saturation(255, 255, 255, 2.2)[1] == 1

    def test_the_status_gives_the_picked_colour_back(self):
        from clControl import _tapo_hue_saturation, _tapo_status_fields
        for rgb in [(255, 105, 180), (116, 10, 255), (255, 165, 0)]:
            hue, sat = _tapo_hue_saturation(*rgb, gamma=2.2)
            info = MagicMock(brightness=100, color_temp=0, hue=hue, saturation=sat)
            back = _tapo_status_fields(info, 2.2)["rgb"]
            assert max(abs(a - b) for a, b in zip(back, rgb)) <= 40, (rgb, back)       # a bulb has whole-number hue and saturation only
            plain = _tapo_status_fields(MagicMock(brightness=100, color_temp=0, hue=hue, saturation=sat), 1.0)["rgb"]
            assert plain != back or rgb == (255, 0, 0)

    @pytest.mark.parametrize("raw, expected", [(1, 1.0), (2.2, 2.2), (0.2, 1.0), (9, 3.0), ("2", 2.0), ("x", 1.0), (None, 1.0), (float("nan"), 1.0)])
    def test_a_stored_value_is_kept_in_range(self, raw, expected):
        from clControl import _clamp_gamma
        assert _clamp_gamma(raw) == expected

    @pytest.mark.asyncio
    async def test_the_bulb_gets_the_corrected_hue_and_saturation(self, manager, mocker):
        device = mocker.MagicMock()
        for name in ("on", "set_brightness", "set_hue_saturation"):
            setattr(device, name, mocker.AsyncMock())
        manager.colour_gamma = lambda: 2.2
        await manager._apply_tapo(device, False, True, False, None, 100, None, rgb=(116, 10, 255))
        device.set_hue_saturation.assert_awaited_once_with(251, 100)
        manager.colour_gamma = lambda: 1.0
        device.set_hue_saturation.reset_mock()
        await manager._apply_tapo(device, False, True, False, None, 100, None, rgb=(116, 10, 255))
        device.set_hue_saturation.assert_awaited_once_with(266, 96)

    def test_the_manager_reads_the_setting_and_falls_back_to_one(self, mocker, tmp_path):
        from clControl import LightManager
        with patch.dict(os.environ, {"TAPO_EMAIL": "t@t.com", "TAPO_PASSWORD": "x"}), patch('utils.clEnvLoader.load_dotenv'):
            m = LightManager()
        core = tmp_path / "config" / "core.json"
        core.parent.mkdir()
        m.base_dir = str(tmp_path / "src")
        assert m.colour_gamma() == 1.0                                            # no file
        core.write_text(json.dumps({"settings": {"light_colour_gamma": 1.8}}))
        assert m.colour_gamma() == 1.8
        core.write_text(json.dumps({"settings": {"light_colour_gamma": "junk"}}))
        assert m.colour_gamma() == 1.0


class TestTheServiceDoesNotDecideBrightness:
    """The pickers decide the brightness of a colour (utils/clColour.py); the service sets exactly what it is told."""

    def _device(self, mocker):
        device = mocker.MagicMock()
        for name in ("on", "set_brightness", "set_hue_saturation"):
            setattr(device, name, mocker.AsyncMock())
        return device

    @pytest.mark.asyncio
    async def test_a_colour_and_a_brightness_are_applied_as_sent(self, manager, mocker):
        device = self._device(mocker)
        manager.colour_gamma = lambda: 2.2
        await manager._apply_tapo(device, False, True, False, None, 13, None, rgb=(0, 255, 0))
        device.set_hue_saturation.assert_awaited_once_with(120, 100)
        device.set_brightness.assert_awaited_once_with(13)

    @pytest.mark.asyncio
    async def test_a_dark_rgb_is_not_dimmed_again_by_the_service(self, manager, mocker):
        device = self._device(mocker)
        await manager._apply_tapo(device, False, True, False, None, 80, None, rgb=(0, 100, 0))
        device.set_brightness.assert_awaited_once_with(80)

    @pytest.mark.asyncio
    async def test_a_colour_with_no_brightness_leaves_the_brightness_alone(self, manager, mocker):
        device = self._device(mocker)
        await manager._apply_tapo(device, False, True, False, None, None, None, rgb=(128, 0, 0))
        device.set_brightness.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_colour_name_keeps_its_old_behaviour(self, manager, mocker):
        device = self._device(mocker)
        manager.color_matrix = {"purple": {"type": "rgb", "r": 128, "g": 0, "b": 128}}
        await manager._apply_tapo(device, False, True, False, "purple", 80, None)
        device.set_brightness.assert_awaited_once_with(80)


class TestUnknownLightTarget:
    """A named light that matches none of the saved ones used to fall through to the legacy default bulb:
    the wrong light changed, silently. The caller now asks which one was meant."""

    @pytest.fixture
    def three(self, manager):
        manager.lights = {"living_room": {"ip": "192.168.1.9", "mac": "AA", "type": "wiz"},
                          "bedroom": {"ip": "192.168.1.10", "mac": "BB", "type": "wiz"},
                          "kitchen": {"ip": "192.168.1.11", "mac": "CC", "type": "wiz"}}
        return manager

    @pytest.mark.parametrize("target", ["garage", "the lamp", "porch light"])
    def test_a_name_that_matches_no_light_is_unknown(self, three, target):
        assert three.unknown_target(target) is True

    @pytest.mark.parametrize("target", [None, "", "all", "lights", "the light", "light color", "the light colour", "everything", "bedroom", "the bedroom light", "living room", "KITCHEN"])
    def test_generic_and_known_names_are_not(self, three, target):
        assert three.unknown_target(target) is False

    def test_without_saved_lights_nothing_is_unknown(self, manager):
        manager.lights = {}
        assert manager.unknown_target("garage") is False

    def test_the_question_lists_the_saved_lights(self, three):
        assert three.light_names_spoken() == "living room, bedroom or kitchen"
        three.lights = {"lamp": {}}
        assert three.light_names_spoken() == "lamp"

    @pytest.mark.asyncio
    async def test_the_listener_asks_instead_of_touching_any_light(self, three, mock_mqtt, message_stream, mocker):
        from clControl import mqtt_service_listener
        control = mocker.patch.object(three, "control_bulb", new=mocker.AsyncMock())
        mock_mqtt.messages = message_stream([
            ("home/room/all/set", json.dumps({"action": "on", "light_target": "garage", "color": "red"})),
        ])

        task = __import__("asyncio").create_task(mqtt_service_listener(three))
        await __import__("asyncio").sleep(0.2)
        task.cancel()

        control.assert_not_called()
        feedback = [json.loads(c[0][1]) for c in mock_mqtt.publish.call_args_list if c[0][0] == "jarvis/feedback"]
        asks = [f for f in feedback if f.get("action") == "request_light_target"]
        assert len(asks) == 1
        assert "garage" in asks[0]["message"] and "kitchen" in asks[0]["message"]
        assert asks[0]["pending"] == {"action": "on", "color": "red"}


class TestStatusPollLogNoise:
    """The 25 s status poll made the tapo library log three INFO lines per light each time."""

    @staticmethod
    def _rec(msg, name="tapo.api.protocol", level=20):
        import logging
        return logging.LogRecord(name, level, __file__, 1, msg, None, None)

    @pytest.mark.parametrize("msg", [
        "Get Device info...",
        'Request: {"method":"get_device_info","params":null}',
        'Device responded with: TapoResponse { error_code: 0, result: Some(DeviceInfoColorLightResult { avatar: "x" }) }',
    ])
    def test_status_read_lines_are_dropped(self, msg):
        from clControl import _is_status_poll_noise
        assert _is_status_poll_noise(self._rec(msg)) is True

    @pytest.mark.parametrize("msg", [
        "Device info will change to: Object {}",
        'Request: {"method":"set_device_info","params":{"device_on":true}}',
        "Device responded with: TapoResponse { error_code: 0, result: None }",
    ])
    def test_command_lines_are_kept(self, msg):
        from clControl import _is_status_poll_noise
        assert _is_status_poll_noise(self._rec(msg)) is False

    def test_other_loggers_and_warnings_are_kept(self):
        from clControl import _is_status_poll_noise
        assert _is_status_poll_noise(self._rec("Get Device info...", name="other")) is False
        assert _is_status_poll_noise(self._rec("Get Device info...", level=30)) is False
