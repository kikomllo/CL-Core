"""The web Settings window's Audio, Keybinds and Presence tabs: the server allow-list, the keybinds file, the pairing info, and the two
pure client modules (web/js/keycapture.js, web/js/presence-model.js) run under node."""
import json
import os
import shutil
import subprocess
import sys

import pytest
import pytest_asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from utils import clWebSettings  # noqa: E402
from utils.clConfigLoader import ConfigLoader  # noqa: E402

BadSetting = clWebSettings.BadSetting


@pytest.fixture
def loader(tmp_path):
    (tmp_path / "core.json").write_text(json.dumps({"settings": {"theme": "jarvis", "automation_settings": {"presence_lights": {"enabled": False, "lights": ["bedroom"]}}}}))
    (tmp_path / "devices.json").write_text(json.dumps({"networks": {"Home": {"bedroom": {"ip": "1"}, "living_room": {"ip": "2"}}, "Work": {"desk": {"ip": "3"}, "bedroom": {"ip": "4"}}}}))
    (tmp_path / "keybinds.json").write_text(json.dumps({
        "abort": {"key": "<ctrl>+<alt>+<shift>+a", "mode": "single"}, "push_to_talk": {"key": "KEY_RIGHTALT", "mode": "continuous"}, "old_style": "<ctrl>+o"}))
    return ConfigLoader(config_dir=str(tmp_path))


def saved(loader, name="core.json"):
    return loader.load_json(name)


class TestPresenceAndWifiGate:
    def test_the_snapshot_lists_the_saved_lights_once_and_the_current_choices(self, loader):
        snap = clWebSettings.snapshot(loader)
        assert snap["light_names"] == ["bedroom", "living_room", "desk"]                 # bedroom is in two networks: listed once
        assert snap["presence_lights"] == {"enabled": False, "lights": ["bedroom"]}
        assert snap["monitor_settings"] == {"wifi_gate_enabled": False, "wifi_mac": ""}

    def test_the_presence_rule_and_the_gate_save_under_their_own_blocks(self, loader):
        clWebSettings.apply(loader, "automation_settings.presence_lights.enabled", True)
        clWebSettings.apply(loader, "monitor_settings.wifi_gate_enabled", True)
        s = saved(loader)["settings"]
        assert s["automation_settings"]["presence_lights"]["enabled"] is True and s["monitor_settings"]["wifi_gate_enabled"] is True
        assert s["automation_settings"]["presence_lights"]["lights"] == ["bedroom"]      # the rest of the rule is untouched

    def test_a_light_is_added_and_removed_without_duplicates(self, loader):
        key = "automation_settings.presence_lights.light."
        clWebSettings.apply(loader, key + "desk", True)
        clWebSettings.apply(loader, key + "desk", True)
        assert saved(loader)["settings"]["automation_settings"]["presence_lights"]["lights"] == ["bedroom", "desk"]
        clWebSettings.apply(loader, key + "bedroom", False)
        assert saved(loader)["settings"]["automation_settings"]["presence_lights"]["lights"] == ["desk"]

    def test_only_a_saved_light_can_be_chosen(self, loader):
        with pytest.raises(BadSetting):
            clWebSettings.apply(loader, "automation_settings.presence_lights.light.kitchen_toaster", True)
        with pytest.raises(BadSetting):
            clWebSettings.check("automation_settings.presence_lights.light.../../x", True)         # not even a light-shaped name

    def test_the_mac_is_trimmed_lowercased_and_can_be_cleared(self, loader):
        assert clWebSettings.apply(loader, "monitor_settings.wifi_mac", "  AA:BB:CC:00:11:22 ") == "aa:bb:cc:00:11:22"
        assert saved(loader)["settings"]["monitor_settings"]["wifi_mac"] == "aa:bb:cc:00:11:22"
        assert clWebSettings.apply(loader, "monitor_settings.wifi_mac", "   ") is None

    @pytest.mark.parametrize("key,value", [("monitor_settings.wifi_gate_enabled", "yes"), ("monitor_settings.wifi_mac", 5), ("monitor_settings.wifi_mac", "x" * 65),
                                           ("monitor_settings.wifi_mac", "a\nb"), ("automation_settings.presence_lights.enabled", 1),
                                           ("automation_settings.presence_lights.lights", ["x"]), ("monitor_settings.other", True)])
    def test_everything_else_is_refused(self, key, value):
        with pytest.raises(BadSetting):
            clWebSettings.check(key, value)


class TestKeybinds:
    def test_the_snapshot_keeps_the_files_order_and_reads_an_old_plain_entry(self, loader):
        snap = clWebSettings.keybinds_snapshot(loader)
        assert list(snap) == ["abort", "push_to_talk", "old_style"]
        assert snap["old_style"] == {"key": "<ctrl>+o", "mode": "single"}

    @pytest.mark.parametrize("value", ["<ctrl>+<alt>+<shift>+a", "<ctrl>+7", "<super>+f", "a", ""])
    def test_a_combo_is_modifiers_plus_one_letter_or_digit(self, value):
        assert clWebSettings.check_keybind("abort", "key", value) == value

    @pytest.mark.parametrize("value", ["<ctrl>+<ctrl>+a", "<ctrl>+ab", "<ctrl>+<f5>", "<ctrl>+F", "ctrl+a", "<ctrl>+", "<ctrl>+a+b", "KEY_SPACE", 5])
    def test_anything_else_is_refused_for_a_combo(self, value):
        with pytest.raises(BadSetting):
            clWebSettings.check_keybind("abort", "key", value)

    @pytest.mark.parametrize("value", ["KEY_RIGHTALT", "KEY_LEFTSHIFT", "KEY_CAPSLOCK", "KEY_SPACE", "KEY_F13", "KEY_F24", ""])
    def test_push_to_talk_takes_one_supported_key(self, value):
        assert clWebSettings.check_keybind("push_to_talk", "key", value) == value

    @pytest.mark.parametrize("value", ["<ctrl>+a", "KEY_A", "KEY_F12", "KEY_F25", "KEY_ENTER"])
    def test_push_to_talk_refuses_the_rest(self, value):
        with pytest.raises(BadSetting):
            clWebSettings.check_keybind("push_to_talk", "key", value)

    def test_the_mode_and_the_field_are_checked(self):
        assert clWebSettings.check_keybind("abort", "mode", "continuous") == "continuous"
        for field, value in (("mode", "double"), ("action", "x"), ("key", None)):
            with pytest.raises(BadSetting):
                clWebSettings.check_keybind("abort", field, value)

    def test_saving_changes_one_field_of_one_existing_shortcut(self, loader):
        clWebSettings.apply_keybind(loader, "abort", "key", "<ctrl>+q")
        clWebSettings.apply_keybind(loader, "push_to_talk", "mode", "single")
        data = saved(loader, "keybinds.json")
        assert data["abort"] == {"key": "<ctrl>+q", "mode": "single"} and data["push_to_talk"] == {"key": "KEY_RIGHTALT", "mode": "single"}

    def test_an_old_plain_entry_is_upgraded_when_edited(self, loader):
        clWebSettings.apply_keybind(loader, "old_style", "mode", "continuous")
        assert saved(loader, "keybinds.json")["old_style"] == {"key": "<ctrl>+o", "mode": "continuous"}

    def test_the_page_cannot_invent_a_shortcut(self, loader):
        before = saved(loader, "keybinds.json")
        with pytest.raises(BadSetting):
            clWebSettings.apply_keybind(loader, "format_disk", "key", "<ctrl>+z")
        assert saved(loader, "keybinds.json") == before


@pytest.mark.asyncio
class TestEndpoints:
    @pytest_asyncio.fixture
    async def client(self, loader, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer
        import clWebUI
        monkeypatch.setattr(clWebUI, "_loader", lambda: loader)
        async with TestClient(TestServer(clWebUI.build_app(None))) as c:
            c.origin = f"http://127.0.0.1:{clWebUI.PORT}"
            yield c

    async def test_get_keybinds(self, client):
        assert list((await (await client.get("/api/keybinds")).json())) == ["abort", "push_to_talk", "old_style"]

    async def test_post_a_keybind(self, client, loader):
        r = await client.post("/api/keybinds", json={"action": "abort", "field": "key", "value": "<alt>+x"}, headers={"Origin": client.origin})
        assert r.status == 200 and (await r.json())["value"] == "<alt>+x"
        assert saved(loader, "keybinds.json")["abort"]["key"] == "<alt>+x"

    async def test_a_bad_or_foreign_keybind_write_changes_nothing(self, client, loader):
        before = saved(loader, "keybinds.json")
        assert (await client.post("/api/keybinds", json={"action": "abort", "field": "key", "value": "<ctrl>+<ctrl>+x"})).status == 400
        assert (await client.post("/api/keybinds", json={"action": "nope", "field": "key", "value": "a"})).status == 400
        assert (await client.post("/api/keybinds", json={"action": "abort"})).status == 400
        assert (await client.post("/api/keybinds", json={"action": "abort", "field": "key", "value": "a"}, headers={"Origin": "http://evil.example"})).status == 403
        assert (await client.post("/api/keybinds", data={"action": "abort", "field": "key", "value": "a"})).status == 403
        assert saved(loader, "keybinds.json") == before

    async def test_choosing_an_unknown_light_is_a_400_not_a_crash(self, client):
        r = await client.post("/api/settings", json={"key": "automation_settings.presence_lights.light.ghost", "value": True})
        assert r.status == 400 and "ghost" in (await r.json())["error"]

    async def test_pairing_info_has_the_uuid_and_the_test_mode_data(self, client):
        from utils import clBeacon
        secret = clBeacon.generate_secret()
        code = clBeacon.encode_secret(secret)
        plain = await (await client.get(f"/api/pairing-info?code={code}")).json()
        assert plain == {"uuid": clBeacon.BEACON_SERVICE_UUID, "data": ""}
        static = await (await client.get(f"/api/pairing-info?code={code}&static=1")).json()
        assert static["data"] == clBeacon.static_payload(secret).hex()
        assert (await (await client.get("/api/pairing-info?code=%21%21&static=1")).json())["data"] == ""      # a bad code: no data, no error


# ---------------------------------------------------------------- the pure client modules, under node
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(module, expr):
    path = os.path.join(ROOT, "web", "js", module).replace("\\", "/")
    script = f'const M = await import("file:///{path}");\nconsole.log(JSON.stringify({expr}));'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
class TestKeyCapture:
    def ev(self, code, **mods):
        return json.dumps({"code": code, "ctrlKey": False, "altKey": False, "shiftKey": False, "metaKey": False, **mods})

    def test_a_combo_is_built_in_the_files_modifier_order(self):
        assert run_js("keycapture.js", f'M.comboFromEvent({self.ev("KeyA", shiftKey=True, ctrlKey=True, altKey=True)})') == {"value": "<ctrl>+<alt>+<shift>+a"}
        assert run_js("keycapture.js", f'M.comboFromEvent({self.ev("Digit7", ctrlKey=True)})') == {"value": "<ctrl>+7"}
        assert run_js("keycapture.js", f'M.comboFromEvent({self.ev("KeyF", metaKey=True)})') == {"value": "<super>+f"}
        assert run_js("keycapture.js", f'M.comboFromEvent({self.ev("KeyQ")})') == {"value": "q"}              # a bare letter is allowed, as in Qt

    def test_a_bare_modifier_waits_and_escape_clears(self):
        for code in ("ControlLeft", "AltRight", "ShiftLeft", "MetaLeft", "AltGraph"):
            assert run_js("keycapture.js", f'M.comboFromEvent({self.ev(code, ctrlKey=True)})') == {"pending": True}
        assert run_js("keycapture.js", f'M.comboFromEvent({self.ev("Escape")})') == {"clear": True}

    def test_other_keys_are_unsupported(self):
        for code in ("F5", "Enter", "Space", "ArrowUp", "Comma", "Numpad1"):
            assert "error" in run_js("keycapture.js", f'M.comboFromEvent({self.ev(code, ctrlKey=True)})')

    def test_push_to_talk_tells_left_from_right_and_takes_f13_to_f24(self):
        got = run_js("keycapture.js", '["ShiftLeft","ShiftRight","ControlLeft","ControlRight","AltLeft","AltRight","CapsLock","Space","F13","F24","F12","KeyA","Enter"].map(M.pttFromCode)')
        assert got == ["KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_CAPSLOCK", "KEY_SPACE", "KEY_F13", "KEY_F24", None, None, None]

    def test_every_key_the_page_produces_is_accepted_by_the_server(self):
        """The two sides must agree: whatever the capture box can produce, the allow-list takes."""
        ptt = run_js("keycapture.js", '["ShiftLeft","ShiftRight","ControlLeft","ControlRight","AltLeft","AltRight","CapsLock","Space"].concat(Array.from({length:12},(_, i)=>"F"+(i+13))).map(M.pttFromCode)')
        for key in ptt:
            assert clWebSettings.check_keybind("push_to_talk", "key", key) == key
        for mods in ("", "<ctrl>+", "<ctrl>+<alt>+", "<ctrl>+<alt>+<shift>+<super>+"):
            for last in ("a", "z", "0", "9"):
                assert clWebSettings.check_keybind("abort", "key", mods + last)

    def test_display_form(self):
        assert run_js("keycapture.js", 'M.pretty("<ctrl>+<alt>+<shift>+a")') == "Ctrl+Alt+Shift+A"
        assert run_js("keycapture.js", 'M.pretty("KEY_RIGHTALT")') == "KEY_RIGHTALT"
        assert run_js("keycapture.js", 'M.pretty("")') == ""


@needs_node
class TestPresenceText:
    @pytest.mark.parametrize("payload,text", [
        (None, "Waiting for the presence monitor..."),
        ({"paired": False}, "No device paired."),
        ({"paired": True, "available": False, "reason": "no adapter"}, "Paired - Bluetooth unavailable (no adapter)."),
        ({"paired": True, "available": False}, "Paired - Bluetooth unavailable (unknown)."),
        ({"paired": True, "present": True, "rssi": -61}, "Paired - here (signal -61 dBm)."),
        ({"paired": True, "present": True}, "Paired - here."),
        ({"paired": True, "present": False}, "Paired - away."),
    ])
    def test_the_status_line(self, payload, text):
        assert run_js("presence-model.js", f"M.presenceText({json.dumps(payload)})") == text

    def test_the_steps_depend_on_test_mode(self):
        assert "Service data" in run_js("presence-model.js", 'M.pairingSteps({static: true})')
        assert "Pairing code" in run_js("presence-model.js", 'M.pairingSteps({static: false})')

    def test_a_finished_attempt_is_reported(self):
        assert run_js("presence-model.js", 'M.pairingResult({state: "paired"})') == "Paired."
        assert run_js("presence-model.js", 'M.pairingResult({state: "failed", reason: "timeout"})') == "Pairing failed: timeout"
        assert run_js("presence-model.js", 'M.pairingResult({state: "failed"})') == "Pairing failed: unknown reason"
        assert run_js("presence-model.js", 'M.pairingResult({state: "waiting"})') == ""

    def test_the_countdown(self):
        assert run_js("presence-model.js", "M.countdownText(1247, 1000)") == "Expires in 4:07."
        assert run_js("presence-model.js", "M.countdownText(1000, 1200)") == "Expires in 0:00."
        assert run_js("presence-model.js", "M.countdownText(null, 5)") == ""
