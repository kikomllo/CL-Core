"""The web dashboard's settings endpoint: an allow-list over core.json that only a same-origin JSON request can write."""
import json
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils import clWebSettings  # noqa: E402
from utils.clConfigLoader import ConfigLoader  # noqa: E402


@pytest.fixture
def loader(tmp_path):
    (tmp_path / "core.json").write_text(json.dumps({"ecosystem": {"mode": "NORMAL"}, "settings": {"theme": "jarvis", "stt_model": "small"}}))
    return ConfigLoader(config_dir=str(tmp_path))


def saved(loader):
    return loader.load_json("core.json")


class TestCheck:
    def test_known_values_pass(self):
        assert clWebSettings.check("theme", "ultron") == "ultron"
        assert clWebSettings.check("silent_mode", True) is True
        assert clWebSettings.check("debug_flags.wakeword_debug_logging", False) is False
        assert clWebSettings.check("start_on_login", True) is True
        assert clWebSettings.check("audio_settings.output_device", "Speakers (USB)") == "Speakers (USB)"

    @pytest.mark.parametrize("key,value", [
        ("theme", "sauron"), ("stt_model", "huge"), ("hardware", None),          # not one of the choices
        ("silent_mode", "false"), ("enable_followup", 1),                       # a string/number is not a boolean
        ("debug_flags.wakeword_debug_logging", "yes"), ("start_on_login", 0),
        ("tts_engine", "kokoro"), ("settings.theme", "ultron"), ("mqtt", {}),   # keys outside the allow-list
        ("debug_flags.rm_rf", True),
        ("audio_settings.input_device", ""), ("audio_settings.input_device", 5), ("audio_settings.output_device", "x" * 301),
        ("audio_settings.volume", "loud"),
    ])
    def test_everything_else_is_refused(self, key, value):
        with pytest.raises(clWebSettings.BadSetting):
            clWebSettings.check(key, value)


class TestApply:
    def test_writes_the_setting_and_keeps_the_rest(self, loader):
        assert clWebSettings.apply(loader, "theme", "ultron") == "ultron"
        core = saved(loader)
        assert core["settings"]["theme"] == "ultron" and core["settings"]["stt_model"] == "small"

    def test_a_debug_flag_lands_under_debug_flags(self, loader):
        clWebSettings.apply(loader, "debug_flags.capture_stt_training_data", True)
        assert saved(loader)["settings"]["debug_flags"] == {"capture_stt_training_data": True}

    def test_ecosystem_mode_keeps_the_supervisors_mode_in_step(self, loader):
        clWebSettings.apply(loader, "ecosystem_state", "debug")
        core = saved(loader)
        assert core["settings"]["ecosystem_state"] == "debug" and core["ecosystem"]["mode"] == "DEBUG"

    def test_a_bad_value_writes_nothing(self, loader):
        before = saved(loader)
        with pytest.raises(clWebSettings.BadSetting):
            clWebSettings.apply(loader, "theme", "sauron")
        assert saved(loader) == before


    def test_an_audio_device_lands_under_audio_settings(self, loader):
        clWebSettings.apply(loader, "audio_settings.input_device", "USB Mic")
        assert saved(loader)["settings"]["audio_settings"] == {"input_device": "USB Mic"}


class TestSnapshot:
    def test_fills_defaults_for_what_core_json_lacks(self, loader):
        snap = clWebSettings.snapshot(loader)
        assert snap["theme"] == "jarvis" and snap["stt_model"] == "small"
        assert snap["silent_mode"] is False and snap["hardware"] == "cpu"
        assert set(snap["debug_flags"]) == set(clWebSettings.DEBUG_FLAGS) and not any(snap["debug_flags"].values())

    def test_a_missing_core_json_is_all_defaults(self, tmp_path):
        class NoFile:
            def load_json(self, name):
                raise OSError("gone")
        assert clWebSettings.snapshot(NoFile())["theme"] == "jarvis"


@pytest.mark.asyncio
class TestEndpoint:
    @pytest_asyncio.fixture
    async def client(self, loader, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer
        import clWebUI
        monkeypatch.setattr(clWebUI, "_loader", lambda: loader)
        monkeypatch.setattr(clWebUI.clAutostart, "is_enabled", lambda *a, **k: False)
        calls = []
        monkeypatch.setattr(clWebUI.clAutostart, "set_enabled", lambda want, *a, **k: calls.append(want) or want)
        async with TestClient(TestServer(clWebUI.build_app(None))) as c:
            c.autostart_calls = calls
            c.origin = f"http://127.0.0.1:{clWebUI.PORT}"
            yield c

    async def test_get_returns_the_settings_and_the_startup_entry(self, client):
        data = await (await client.get("/api/settings")).json()
        assert data["theme"] == "jarvis" and data["start_on_login"] is False

    async def test_post_saves_one_setting(self, client, loader):
        r = await client.post("/api/settings", json={"key": "theme", "value": "ultron"}, headers={"Origin": client.origin})
        assert r.status == 200 and (await r.json()) == {"key": "theme", "value": "ultron"}
        assert saved(loader)["settings"]["theme"] == "ultron"

    async def test_start_on_login_goes_to_the_os_not_core_json(self, client, loader):
        r = await client.post("/api/settings", json={"key": "start_on_login", "value": True})
        assert (await r.json())["value"] is True and client.autostart_calls == [True]
        assert "start_on_login" not in saved(loader)["settings"]

    async def test_a_page_on_another_origin_is_refused(self, client, loader):
        r = await client.post("/api/settings", json={"key": "theme", "value": "ultron"}, headers={"Origin": "http://evil.example"})
        assert r.status == 403 and saved(loader)["settings"]["theme"] == "jarvis"

    async def test_a_form_post_is_refused(self, client, loader):
        r = await client.post("/api/settings", data={"key": "theme", "value": "ultron"})
        assert r.status == 403 and saved(loader)["settings"]["theme"] == "jarvis"

    async def test_the_device_list_marks_the_saved_device(self, client, loader, monkeypatch):
        import clWebUI
        clWebUI._device_cache.clear()
        monkeypatch.setattr(clWebUI, "list_devices", lambda kind: [{"value": "System Default", "label": "System Default"},
                                                                    {"value": f"{kind} one", "label": "One"}])
        clWebSettings.apply(loader, "audio_settings.output_device", "output one")
        data = await (await client.get("/api/audio-devices?kind=output")).json()
        assert data["current"] == "output one" and [o["value"] for o in data["options"]] == ["System Default", "output one"]
        assert (await client.get("/api/audio-devices?kind=bogus")).status == 400

    async def test_a_bad_key_or_value_is_a_400(self, client, loader):
        for body in ({"key": "mqtt", "value": 1}, {"key": "theme", "value": "sauron"}, {"value": "x"}, {"key": "theme"}):
            r = await client.post("/api/settings", json=body)
            assert r.status == 400, body
        assert saved(loader)["settings"]["theme"] == "jarvis"


class TestLightSwatches:
    """The colour picker's eight swatches the user can change: one stored colour per slot, a slot with none is the default."""

    def test_a_valid_colour_for_a_known_slot_is_stored_under_light_swatches(self, loader):
        assert clWebSettings.apply(loader, "light_swatch.red", [10, 20, 30]) == [10, 20, 30]
        assert clWebSettings.apply(loader, "light_swatch.hot pink", (255, 0, 128)) == [255, 0, 128]
        assert saved(loader)["settings"]["light_swatches"] == {"red": [10, 20, 30], "hot pink": [255, 0, 128]}
        assert saved(loader)["settings"]["theme"] == "jarvis"           # nothing else was touched

    def test_null_puts_the_default_back_and_the_last_one_drops_the_block(self, loader):
        clWebSettings.apply(loader, "light_swatch.red", [1, 2, 3])
        clWebSettings.apply(loader, "light_swatch.blue", [4, 5, 6])
        assert clWebSettings.apply(loader, "light_swatch.red", None) is None
        assert saved(loader)["settings"]["light_swatches"] == {"blue": [4, 5, 6]}
        clWebSettings.apply(loader, "light_swatch.blue", None)
        assert "light_swatches" not in saved(loader)["settings"]
        clWebSettings.apply(loader, "light_swatch.green", None)          # resetting what was never changed is not an error
        assert "light_swatches" not in saved(loader)["settings"]

    @pytest.mark.parametrize("key,value", [
        ("light_swatch.magenta", [1, 2, 3]), ("light_swatch.", [1, 2, 3]), ("light_swatch.warm white", [1, 2, 3]),   # not a colour slot
        ("light_swatch.red", [1, 2]), ("light_swatch.red", [1, 2, 3, 4]), ("light_swatch.red", "red"),
        ("light_swatch.red", [256, 0, 0]), ("light_swatch.red", [-1, 0, 0]), ("light_swatch.red", [1.5, 0, 0]),
        ("light_swatch.red", [True, 0, 0]), ("light_swatch.red", {"r": 1}), ("light_swatch.red", 5),
    ])
    def test_anything_else_is_refused_and_writes_nothing(self, loader, key, value):
        with pytest.raises(clWebSettings.BadSetting):
            clWebSettings.apply(loader, key, value)
        assert "light_swatches" not in saved(loader)["settings"]

    def test_the_snapshot_carries_only_the_changed_valid_swatches(self, loader):
        assert clWebSettings.snapshot(loader)["light_swatches"] == {}
        clWebSettings.apply(loader, "light_swatch.cyan", [0, 100, 200])
        assert clWebSettings.snapshot(loader)["light_swatches"] == {"cyan": [0, 100, 200]}

        def corrupt(core):                                               # a hand-edited file must not break the picker
            core["settings"]["light_swatches"] = {"cyan": [0, 100, 200], "red": "nope", "magenta": [1, 2, 3], "blue": [999, 0, 0]}
        loader.update_json_atomic("core.json", corrupt)
        assert clWebSettings.snapshot(loader)["light_swatches"] == {"cyan": [0, 100, 200]}

    def test_the_slots_are_the_voice_colours_the_pickers_default_to(self):
        entities = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config", "entities.json"), encoding="utf-8"))["colors"]
        for name in clWebSettings.SWATCH_NAMES:
            assert name in entities and entities[name]["type"] == "rgb", name
