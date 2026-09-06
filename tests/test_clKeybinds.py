import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from pynput import keyboard
from clKeybinds import PTT_KEY_MAP, is_ptt_key


class TestPttKeyMap:
    """PTT_KEY_MAP replaced a hardcoded if/elif that only recognized
    Left/Right Alt, silently doing nothing for any other push_to_talk.key
    configured on Windows (evdev resolves any KEY_* name generically on
    Linux, pynput has no equivalent generic API)."""

    @pytest.mark.parametrize("evdev_name,expected_members", [
        ("KEY_RIGHTALT", {keyboard.Key.alt_r, keyboard.Key.alt_gr}),
        ("KEY_LEFTALT", {keyboard.Key.alt_l, keyboard.Key.alt}),
        ("KEY_RIGHTCTRL", {keyboard.Key.ctrl_r}),
        ("KEY_LEFTCTRL", {keyboard.Key.ctrl_l, keyboard.Key.ctrl}),
        ("KEY_RIGHTSHIFT", {keyboard.Key.shift_r}),
        ("KEY_LEFTSHIFT", {keyboard.Key.shift}),
        ("KEY_CAPSLOCK", {keyboard.Key.caps_lock}),
        ("KEY_SPACE", {keyboard.Key.space}),
    ])
    def test_known_keys_map_to_expected_pynput_members(self, evdev_name, expected_members):
        assert set(PTT_KEY_MAP[evdev_name]) == expected_members

    @pytest.mark.parametrize("f_num", range(13, 25))
    def test_f13_through_f24_are_mapped(self, f_num):
        assert PTT_KEY_MAP[f"KEY_F{f_num}"] == (getattr(keyboard.Key, f"f{f_num}"),)

    def test_unmapped_key_name_is_absent(self):
        # Anything not in the realistic PTT candidate set is deliberately
        # left out -- is_ptt_key handles the miss gracefully (see below).
        assert "KEY_Q" not in PTT_KEY_MAP


class TestIsPttKey:
    def test_matches_configured_key(self):
        assert is_ptt_key(keyboard.Key.ctrl_r, "KEY_RIGHTCTRL") is True

    def test_does_not_match_a_different_key(self):
        assert is_ptt_key(keyboard.Key.ctrl_l, "KEY_RIGHTCTRL") is False

    def test_leftalt_also_matches_generic_alt(self):
        """pynput has no shift_l equivalent for generic 'alt' being reported
        as bare Key.alt on some backends -- LEFTALT must accept both."""
        assert is_ptt_key(keyboard.Key.alt, "KEY_LEFTALT") is True
        assert is_ptt_key(keyboard.Key.alt_l, "KEY_LEFTALT") is True

    def test_rightalt_matches_raw_altgr_vk_fallback(self):
        fake_key = type("FakeKey", (), {"vk": 65027})()
        assert is_ptt_key(fake_key, "KEY_RIGHTALT") is True

    def test_altgr_vk_fallback_does_not_apply_to_other_keys(self):
        fake_key = type("FakeKey", (), {"vk": 65027})()
        assert is_ptt_key(fake_key, "KEY_LEFTCTRL") is False

    def test_unconfigured_key_name_never_matches(self):
        assert is_ptt_key(keyboard.Key.space, "KEY_UNKNOWN_FUTURE_KEY") is False

    def test_f13_is_recognized_when_configured(self):
        """Previously only Left/Right Alt worked at all -- F13 (a realistic
        dedicated PTT key on many keyboards) must now work too."""
        assert is_ptt_key(keyboard.Key.f13, "KEY_F13") is True
        assert is_ptt_key(keyboard.Key.f14, "KEY_F13") is False
