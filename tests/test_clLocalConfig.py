import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "utils")))

from clLocalConfig import apply_profile, ensure_core, fill_missing
from clConfigLoader import ConfigLoader

REPO_CONFIG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config"))


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def cfg(tmp_path):
    _write(tmp_path / "core.default.json", {"settings": {"hardware": "cuda", "slm": {"threads": 4, "new": 1}, "langs": ["en"]}})
    _write(tmp_path / "profiles.json", {"lite": {"hardware": "cpu", "slm": {"threads": 2}}})
    return tmp_path


class TestFillMissing:
    def test_adds_only_absent_keys_and_never_overwrites(self):
        local = {"a": 1, "d": {"x": 5}, "l": [1]}
        assert fill_missing(local, {"a": 9, "b": 2, "d": {"x": 0, "y": 3}, "l": [2, 3]}) is True
        assert local == {"a": 1, "b": 2, "d": {"x": 5, "y": 3}, "l": [1]}

    def test_reports_no_change(self):
        assert fill_missing({"a": 1}, {"a": 2}) is False


class TestEnsureCore:
    def test_creates_core_from_the_template(self, cfg):
        assert ensure_core(str(cfg)) is True
        assert json.loads((cfg / "core.json").read_text())["settings"]["hardware"] == "cuda"

    def test_keeps_machine_values_and_adds_new_template_keys(self, cfg):
        _write(cfg / "core.json", {"settings": {"hardware": "cpu", "slm": {"threads": 9}}})
        assert ensure_core(str(cfg)) is True
        s = json.loads((cfg / "core.json").read_text())["settings"]
        assert s["hardware"] == "cpu" and s["slm"]["threads"] == 9 and s["slm"]["new"] == 1 and s["langs"] == ["en"]

    def test_up_to_date_file_is_not_rewritten(self, cfg):
        ensure_core(str(cfg))
        assert ensure_core(str(cfg)) is False

    def test_no_template_does_nothing(self, tmp_path):
        assert ensure_core(str(tmp_path)) is False
        assert not (tmp_path / "core.json").exists()


class TestProfiles:
    def test_applies_values_deeply_and_records_the_name(self, cfg):
        apply_profile("lite", str(cfg))
        s = json.loads((cfg / "core.json").read_text())["settings"]
        assert s["hardware"] == "cpu" and s["slm"] == {"threads": 2, "new": 1} and s["performance_profile"] == "lite"

    def test_unknown_profile_is_an_error(self, cfg):
        with pytest.raises(KeyError):
            apply_profile("turbo", str(cfg))


class TestConfigLoaderFallback:
    def test_load_json_builds_a_missing_core_json(self, cfg):
        assert ConfigLoader(str(cfg)).load_json("core.json")["settings"]["hardware"] == "cuda"

    def test_other_missing_files_still_raise(self, cfg):
        with pytest.raises(FileNotFoundError):
            ConfigLoader(str(cfg)).load_json("nope.json")


class TestShippedFiles:
    def test_every_profile_key_exists_in_the_template(self):
        default = json.load(open(os.path.join(REPO_CONFIG, "core.default.json"), encoding="utf-8"))["settings"]
        profiles = json.load(open(os.path.join(REPO_CONFIG, "profiles.json"), encoding="utf-8"))

        def check(values, tree, path):
            for k, v in values.items():
                assert k in tree, f"profile key {path}{k} is not in core.default.json"
                if isinstance(v, dict):
                    check(v, tree[k], f"{path}{k}.")

        for name, values in profiles.items():
            check(values, default, f"{name}: ")

    def test_template_has_no_machine_specific_audio_devices(self):
        audio = json.load(open(os.path.join(REPO_CONFIG, "core.default.json"), encoding="utf-8"))["settings"]["audio_settings"]
        assert audio["input_device"] is None and audio["output_device"] == "System Default"
