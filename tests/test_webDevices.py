"""The web Settings window's Lights and Spotify tabs: the `.env` allow-list (secrets never leave the server) and the devices.json operations."""
import json
import os
import sys

import pytest
import pytest_asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from utils import clWebDevices  # noqa: E402
from utils.clConfigLoader import ConfigLoader  # noqa: E402

BadValue = clWebDevices.BadValue


@pytest.fixture
def env_path(tmp_path):
    path = tmp_path / ".env"
    path.write_text('SPOTIPY_CLIENT_ID=abc123\nSPOTIPY_CLIENT_SECRET=topsecret\nTAPO_PASSWORD="p@ss word"\nLIGHT_TYPE=wiz\nUNRELATED=keep me\n', encoding="utf-8")
    return str(path)


@pytest.fixture
def loader(tmp_path):
    (tmp_path / "devices.json").write_text(json.dumps({"networks": {
        "Home": {"living_room": {"ip": "192.168.1.88", "mac": "cc40851c1118", "type": "wiz"}, "bedroom": {"ip": "192.168.1.111", "mac": "10:5A:95:B7:0C:86", "type": "tapo"}},
        "Work": {"desk": {"ip": "10.0.0.5", "mac": "", "type": "wiz"}}}}))
    return ConfigLoader(config_dir=str(tmp_path))


def nets(loader):
    return loader.load_json("devices.json")["networks"]


class TestEnvChecks:
    @pytest.mark.parametrize("key,value", [
        ("LIGHT_TYPE", "tapo"), ("LIGHT_TYPE", ""), ("LIGHT_IP", "192.168.1.9"), ("LIGHT_IP", "fe80::1"), ("LIGHT_MAC", "aa:bb:cc:dd:ee:ff"),
        ("LIGHT_MAC", "aabbccddeeff"), ("TAPO_EMAIL", "me@example.com"), ("TAPO_PASSWORD", "p@ss word!"), ("SPOTIPY_CLIENT_ID", "abc"),
        ("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"), ("SPOTIPY_REDIRECT_URI", ""),
    ])
    def test_good_values_pass(self, key, value):
        assert clWebDevices.check_env(key, value) == value

    @pytest.mark.parametrize("key,value", [
        ("LIGHT_TYPE", "zigbee"), ("LIGHT_IP", "not an ip"), ("LIGHT_IP", "999.1.1.1"), ("LIGHT_MAC", "xyz"), ("SPOTIPY_REDIRECT_URI", "ftp://x"),
        ("SPOTIPY_CLIENT_ID", "a\nEVIL=1"), ("TAPO_PASSWORD", "a\rb"), ("TAPO_EMAIL", "x" * 301), ("TAPO_EMAIL", 5),      # one line, short, text
        ("PATH", "/tmp"), ("UNRELATED", "x"), ("spotipy_client_id", "x"),                                              # not on the list
    ])
    def test_everything_else_is_refused(self, key, value):
        with pytest.raises(BadValue):
            clWebDevices.check_env(key, value)

    def test_values_are_trimmed(self):
        assert clWebDevices.check_env("SPOTIPY_CLIENT_ID", "  abc ") == "abc"


class TestEnvFile:
    def test_a_read_never_contains_a_secret(self, env_path):
        snap = clWebDevices.env_snapshot(env_path)
        assert snap["values"]["SPOTIPY_CLIENT_ID"] == "abc123" and snap["values"]["LIGHT_TYPE"] == "wiz"
        assert snap["secrets"] == {"TAPO_PASSWORD": True, "SPOTIPY_CLIENT_SECRET": True}
        assert "topsecret" not in json.dumps(snap) and "p@ss" not in json.dumps(snap)

    def test_an_unset_secret_reads_as_not_set(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("SPOTIPY_CLIENT_ID=x\n", encoding="utf-8")
        assert clWebDevices.env_snapshot(str(path))["secrets"] == {"TAPO_PASSWORD": False, "SPOTIPY_CLIENT_SECRET": False}

    def test_a_missing_file_reads_as_empty(self, tmp_path):
        snap = clWebDevices.env_snapshot(str(tmp_path / "nope.env"))
        assert snap["values"]["LIGHT_IP"] == "" and not any(snap["secrets"].values())

    def test_a_write_changes_one_key_and_keeps_the_other_lines(self, env_path):
        result = clWebDevices.apply_env(env_path, "SPOTIPY_CLIENT_ID", "newid")
        assert result == {"key": "SPOTIPY_CLIENT_ID", "secret": False, "set": True, "value": "newid"}
        text = open(env_path, encoding="utf-8").read()
        assert "newid" in text and "topsecret" in text and "UNRELATED=keep me" in text and "abc123" not in text

    def test_writing_a_secret_replaces_it_and_does_not_echo_it(self, env_path):
        result = clWebDevices.apply_env(env_path, "SPOTIPY_CLIENT_SECRET", "brand-new-secret")
        assert result == {"key": "SPOTIPY_CLIENT_SECRET", "secret": True, "set": True}
        assert "brand-new-secret" in open(env_path, encoding="utf-8").read() and "topsecret" not in open(env_path, encoding="utf-8").read()
        assert "brand-new-secret" not in json.dumps(clWebDevices.env_snapshot(env_path))

    def test_a_value_with_spaces_or_quotes_round_trips(self, env_path):
        clWebDevices.apply_env(env_path, "TAPO_PASSWORD", "it's a \"p@ss\" word")
        from dotenv import dotenv_values
        assert dotenv_values(env_path)["TAPO_PASSWORD"] == "it's a \"p@ss\" word"

    def test_a_missing_env_file_is_created(self, tmp_path):
        path = str(tmp_path / ".env")
        clWebDevices.apply_env(path, "LIGHT_TYPE", "tapo")
        assert clWebDevices.env_snapshot(path)["values"]["LIGHT_TYPE"] == "tapo"

    def test_a_refused_write_leaves_the_file_alone(self, env_path):
        before = open(env_path, encoding="utf-8").read()
        with pytest.raises(BadValue):
            clWebDevices.apply_env(env_path, "SPOTIPY_CLIENT_ID", "a\nEVIL=1")
        assert open(env_path, encoding="utf-8").read() == before


class TestDeviceOps:
    def test_the_snapshot_lists_networks_and_lights(self, loader):
        snap = clWebDevices.devices_snapshot(loader)
        assert list(snap["networks"]) == ["Home", "Work"] and snap["networks"]["Home"]["bedroom"]["type"] == "tapo"

    def test_an_old_flat_file_is_read_as_one_network(self, tmp_path):
        (tmp_path / "devices.json").write_text(json.dumps({"lights": {"lamp": {"ip": "1.2.3.4", "mac": "", "type": "wiz"}}}))
        loader = ConfigLoader(config_dir=str(tmp_path))
        assert list(clWebDevices.devices_snapshot(loader)["networks"]) == ["Home Network"]
        clWebDevices.apply_device_op(loader, "add_network", {"network": "Other"})
        data = loader.load_json("devices.json")
        assert "lights" not in data and list(data["networks"]) == ["Home Network", "Other"]

    def test_add_and_remove_a_network(self, loader):
        clWebDevices.apply_device_op(loader, "add_network", {"network": "  Cafe  "})
        assert nets(loader)["Cafe"] == {}
        clWebDevices.apply_device_op(loader, "remove_network", {"network": "Cafe"})
        assert "Cafe" not in nets(loader)

    def test_a_new_light_gets_the_qt_defaults_and_a_cleaned_name(self, loader):
        clWebDevices.apply_device_op(loader, "add_light", {"network": "Home", "name": " Kitchen Strip "})
        assert nets(loader)["Home"]["kitchen_strip"] == {"ip": "192.168.1.100", "mac": "", "type": "wiz"}

    def test_edit_one_field_of_one_light(self, loader):
        clWebDevices.apply_device_op(loader, "update_light", {"network": "Home", "name": "bedroom", "field": "ip", "value": "192.168.1.120"})
        clWebDevices.apply_device_op(loader, "update_light", {"network": "Home", "name": "bedroom", "field": "type", "value": "wiz"})
        clWebDevices.apply_device_op(loader, "update_light", {"network": "Home", "name": "bedroom", "field": "mac", "value": ""})
        assert nets(loader)["Home"]["bedroom"] == {"ip": "192.168.1.120", "mac": "", "type": "wiz"}
        assert nets(loader)["Home"]["living_room"]["ip"] == "192.168.1.88"                    # the others are untouched

    def test_a_rename_keeps_the_lights_order_and_data(self, loader):
        clWebDevices.apply_device_op(loader, "update_light", {"network": "Home", "name": "living_room", "field": "name", "value": "Lounge"})
        assert list(nets(loader)["Home"]) == ["lounge", "bedroom"] and nets(loader)["Home"]["lounge"]["ip"] == "192.168.1.88"

    def test_remove_a_light(self, loader):
        clWebDevices.apply_device_op(loader, "remove_light", {"network": "Home", "name": "bedroom"})
        assert list(nets(loader)["Home"]) == ["living_room"]

    def test_move_a_light_to_another_network(self, loader):
        clWebDevices.apply_device_op(loader, "move_light", {"network": "Home", "name": "bedroom", "to": "Work"})
        assert "bedroom" not in nets(loader)["Home"] and nets(loader)["Work"]["bedroom"]["type"] == "tapo"
        clWebDevices.apply_device_op(loader, "move_light", {"network": "Work", "name": "desk", "to": "Work"})       # to itself: nothing happens
        assert "desk" in nets(loader)["Work"]

    @pytest.mark.parametrize("op,args", [
        ("add_network", {"network": "Home"}), ("add_network", {"network": ""}), ("add_network", {"network": "a\nb"}), ("add_network", {"network": 5}),
        ("remove_network", {"network": "Nowhere"}), ("add_light", {"network": "Nowhere", "name": "x"}), ("add_light", {"network": "Home", "name": "bedroom"}),
        ("add_light", {"network": "Home", "name": "../x"}), ("add_light", {"network": "Home", "name": ""}), ("remove_light", {"network": "Home", "name": "ghost"}),
        ("update_light", {"network": "Home", "name": "bedroom", "field": "ip", "value": "nope"}), ("update_light", {"network": "Home", "name": "bedroom", "field": "type", "value": "zigbee"}),
        ("update_light", {"network": "Home", "name": "bedroom", "field": "mac", "value": "zz"}), ("update_light", {"network": "Home", "name": "bedroom", "field": "color", "value": "red"}),
        ("update_light", {"network": "Home", "name": "bedroom", "field": "name", "value": "living_room"}),                  # would overwrite another light
        ("move_light", {"network": "Home", "name": "bedroom", "to": "Nowhere"}), ("move_light", {"network": "Home", "name": "ghost", "to": "Work"}),
        ("drop_everything", {}),
    ])
    def test_a_bad_operation_changes_nothing(self, loader, op, args):
        before = nets(loader)
        with pytest.raises(BadValue):
            clWebDevices.apply_device_op(loader, op, args)
        assert nets(loader) == before

    def test_a_move_cannot_overwrite_a_light_with_the_same_name(self, loader):
        clWebDevices.apply_device_op(loader, "add_light", {"network": "Work", "name": "bedroom"})
        before = nets(loader)
        with pytest.raises(BadValue):
            clWebDevices.apply_device_op(loader, "move_light", {"network": "Home", "name": "bedroom", "to": "Work"})
        assert nets(loader) == before


@pytest.mark.asyncio
class TestEndpoints:
    @pytest_asyncio.fixture
    async def client(self, loader, env_path, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer
        import clWebUI
        monkeypatch.setattr(clWebUI, "_loader", lambda: loader)
        monkeypatch.setattr(clWebUI, "ENV_PATH", env_path)
        async with TestClient(TestServer(clWebUI.build_app(None))) as c:
            c.origin = f"http://127.0.0.1:{clWebUI.PORT}"
            yield c

    async def test_get_devices_and_env(self, client):
        assert list((await (await client.get("/api/devices")).json())["networks"]) == ["Home", "Work"]
        body = await (await client.get("/api/env")).text()
        assert "abc123" in body and "topsecret" not in body and "p@ss" not in body            # the secrets are not in the response at all

    async def test_a_device_operation_returns_the_new_list(self, client, loader):
        r = await client.post("/api/devices", json={"op": "add_light", "network": "Work", "name": "lamp"}, headers={"Origin": client.origin})
        assert r.status == 200 and "lamp" in (await r.json())["networks"]["Work"] and "lamp" in nets(loader)["Work"]

    async def test_a_bad_device_operation_is_a_400_and_changes_nothing(self, client, loader):
        before = nets(loader)
        for body in ({"op": "remove_network", "network": "Nowhere"}, {"op": "nope"}, {"network": "Home"}, ["op"], {"op": "update_light", "network": "Home", "name": "bedroom", "field": "ip", "value": "x"}):
            assert (await client.post("/api/devices", json=body)).status == 400, body
        assert nets(loader) == before

    async def test_a_write_from_another_page_or_a_form_is_refused(self, client, loader, env_path):
        before_env = open(env_path, encoding="utf-8").read()
        before = nets(loader)
        evil = {"Origin": "http://evil.example"}
        assert (await client.post("/api/devices", json={"op": "remove_network", "network": "Home"}, headers=evil)).status == 403
        assert (await client.post("/api/devices", data={"op": "remove_network", "network": "Home"})).status == 403
        assert (await client.post("/api/env", json={"key": "TAPO_PASSWORD", "value": "stolen"}, headers=evil)).status == 403
        assert (await client.post("/api/env", data={"key": "TAPO_PASSWORD", "value": "stolen"})).status == 403
        assert nets(loader) == before and open(env_path, encoding="utf-8").read() == before_env

    async def test_post_env_stores_a_secret_without_echoing_it(self, client, env_path):
        r = await client.post("/api/env", json={"key": "TAPO_PASSWORD", "value": "new-pass"}, headers={"Origin": client.origin})
        d = await r.json()
        assert r.status == 200 and d == {"key": "TAPO_PASSWORD", "secret": True, "set": True} and "new-pass" in open(env_path, encoding="utf-8").read()
        assert "new-pass" not in await (await client.get("/api/env")).text()

    async def test_a_bad_env_write_is_a_400(self, client, env_path):
        before = open(env_path, encoding="utf-8").read()
        for body in ({"key": "PATH", "value": "x"}, {"key": "LIGHT_IP", "value": "nope"}, {"key": "TAPO_EMAIL", "value": "a\nb=1"}, {"key": "TAPO_EMAIL"}, {"value": "x"}):
            assert (await client.post("/api/env", json=body)).status == 400, body
        assert open(env_path, encoding="utf-8").read() == before
