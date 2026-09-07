import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent, QFocusEvent
from PyQt6.QtWidgets import QComboBox


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def make_key_event(key, modifiers=Qt.KeyboardModifier.NoModifier, native_vk=0, native_scan=0):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, native_scan, native_vk, 0)


class TestKeybindCaptureEditComboMode:
    """Combo-mode keybinds (everything except push_to_talk) must only ever
    commit a modifier+letter/number string, since that's the one format both
    clKeybinds.py's pynput HotKey.parse() (Windows) and parse_evdev_hotkey()
    (Linux) parse identically -- anything else risks a keybind that silently
    fails to parse on whichever machine didn't set it."""

    def _edit(self, qapp, current="", is_ptt=False):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit(current, is_ptt=is_ptt)
        e.recording = True
        return e

    def test_ctrl_alt_shift_letter_combo(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier
        e.keyPressEvent(make_key_event(Qt.Key.Key_A, mods))

        assert captured == ["<ctrl>+<alt>+<shift>+a"]

    def test_bare_letter_no_modifiers(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        e.keyPressEvent(make_key_event(Qt.Key.Key_F))

        assert captured == ["f"]

    def test_digit_key(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        e.keyPressEvent(make_key_event(Qt.Key.Key_5, Qt.KeyboardModifier.ControlModifier))

        assert captured == ["<ctrl>+5"]

    def test_bare_modifier_press_does_not_commit(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        e.keyPressEvent(make_key_event(Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier))

        assert captured == []
        assert e.recording is True

    def test_unsupported_main_key_does_not_commit(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        e.keyPressEvent(make_key_event(Qt.Key.Key_Return))

        assert captured == []
        assert e.recording is True
        assert "Unsupported" in e.text()

    def test_escape_clears_keybind(self, qapp):
        e = self._edit(qapp, current="<ctrl>+<alt>+<shift>+a")
        captured = []
        e.committed.connect(captured.append)

        e.keyPressEvent(make_key_event(Qt.Key.Key_Escape))

        assert captured == [""]
        assert e.text() == ""

    def test_autorepeat_is_ignored(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier, 0, 0, 0, "f", True)
        e.keyPressEvent(event)

        assert captured == []


class TestKeybindCaptureEditPushToTalkMode:
    """push_to_talk captures a single raw, left/right-aware key restricted
    to clKeybinds.py's PTT_KEY_MAP set, resolved via native VK code
    (Windows) or native scan code - 8 (Linux evdev)."""

    def _edit(self, qapp, current=""):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit(current, is_ptt=True)
        e.recording = True
        return e

    def test_windows_right_alt_resolves(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        with patch("sys.platform", "win32"):
            e.keyPressEvent(make_key_event(Qt.Key.Key_Alt, native_vk=0xA5))

        assert captured == ["KEY_RIGHTALT"]

    def test_windows_left_ctrl_resolves(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        with patch("sys.platform", "win32"):
            e.keyPressEvent(make_key_event(Qt.Key.Key_Control, native_vk=0xA2))

        assert captured == ["KEY_LEFTCTRL"]

    def test_linux_right_alt_resolves_from_scancode(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        with patch("sys.platform", "linux"):
            e.keyPressEvent(make_key_event(Qt.Key.Key_Alt, native_scan=108))  # 100 + 8

        assert captured == ["KEY_RIGHTALT"]

    def test_unsupported_key_does_not_commit(self, qapp):
        e = self._edit(qapp)
        captured = []
        e.committed.connect(captured.append)

        with patch("sys.platform", "win32"):
            e.keyPressEvent(make_key_event(Qt.Key.Key_A, native_vk=0x41))

        assert captured == []
        assert e.recording is True
        assert "Unsupported" in e.text()

    def test_escape_clears_ptt_keybind_too(self, qapp):
        e = self._edit(qapp, current="KEY_RIGHTALT")
        captured = []
        e.committed.connect(captured.append)

        e.keyPressEvent(make_key_event(Qt.Key.Key_Escape))

        assert captured == [""]


class TestKeybindCaptureEditFocusBehavior:
    def test_focus_in_starts_recording_and_shows_prompt(self, qapp):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit("<ctrl>+<alt>+<shift>+a", is_ptt=False)
        e.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
        assert e.recording is True
        assert "Press" in e.text()

    def test_focus_out_without_committing_reverts_display(self, qapp):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit("<ctrl>+<alt>+<shift>+a", is_ptt=False)
        e.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
        e.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))

        assert e.recording is False
        assert e.text() == "Ctrl+Alt+Shift+A"


class TestKeybindCaptureEditDisplayFormatting:
    """The saved/parsed value must stay exactly '<ctrl>+<alt>+<shift>+a' --
    clKeybinds.py's MODIFIER_MAP lookup and pynput's bracket convention both
    require it -- but the box should show the friendlier 'Ctrl+Alt+Shift+A'
    to the user."""

    def test_construction_shows_pretty_form(self, qapp):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit("<ctrl>+<alt>+<shift>+a", is_ptt=False)
        assert e.text() == "Ctrl+Alt+Shift+A"

    def test_bare_letter_shows_uppercase(self, qapp):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit("f", is_ptt=False)
        assert e.text() == "F"

    def test_committed_signal_still_carries_the_raw_backend_format(self, qapp):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit("", is_ptt=False)
        e.recording = True
        captured = []
        e.committed.connect(captured.append)

        mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier
        e.keyPressEvent(make_key_event(Qt.Key.Key_A, mods))

        assert captured == ["<ctrl>+<alt>+<shift>+a"]
        assert e.text() == "Ctrl+Alt+Shift+A"

    def test_ptt_format_is_left_untouched(self, qapp):
        from ui.clSettingsWidget import KeybindCaptureEdit
        e = KeybindCaptureEdit("KEY_RIGHTALT", is_ptt=True)
        assert e.text() == "KEY_RIGHTALT"


class TestCreateDropdownEliding:
    """Audio device names are often too long for the dropdown's fixed width
    and used to just get clipped mid-character with no way to tell which
    device it was. elide=True swaps in ElidedComboBox (paints '...' instead
    of clipping) and keeps a tooltip with the full name in sync."""

    def _fake_widget(self):
        from ui.clSettingsWidget import SettingsWidget
        fake = SettingsWidget.__new__(SettingsWidget)
        fake.ui_elements = {}
        return fake

    def test_elide_true_uses_elided_combo_box(self, qapp):
        from ui.clSettingsWidget import SettingsWidget, ElidedComboBox
        fake = self._fake_widget()
        wrapper = SettingsWidget._create_dropdown(fake, "k", "Label", ["A", "B"], "A", lambda v: None, elide=True)
        assert isinstance(fake.ui_elements["k"], ElidedComboBox)

    def test_elide_false_uses_plain_combo_box(self, qapp):
        from ui.clSettingsWidget import SettingsWidget, ElidedComboBox
        fake = self._fake_widget()
        wrapper = SettingsWidget._create_dropdown(fake, "k", "Label", ["A", "B"], "A", lambda v: None)
        combo = fake.ui_elements["k"]
        assert isinstance(combo, QComboBox)
        assert not isinstance(combo, ElidedComboBox)

    def test_elide_sets_initial_tooltip_and_updates_on_change(self, qapp):
        from ui.clSettingsWidget import SettingsWidget
        fake = self._fake_widget()
        options = ["Microphone (G435 Wireless Gaming Headset)", "Microphone (Realtek Audio)"]
        wrapper = SettingsWidget._create_dropdown(fake, "k", "Label", options, options[0], lambda v: None, elide=True)
        combo = fake.ui_elements["k"]

        assert combo.toolTip() == options[0]
        combo.setCurrentText(options[1])
        assert combo.toolTip() == options[1]

    def test_non_elided_dropdown_has_no_tooltip_wiring(self, qapp):
        from ui.clSettingsWidget import SettingsWidget
        fake = self._fake_widget()
        wrapper = SettingsWidget._create_dropdown(fake, "k", "Label", ["A", "B"], "A", lambda v: None)
        combo = fake.ui_elements["k"]

        combo.setCurrentText("B")
        assert combo.toolTip() == ""


class TestCreateDropdownDisplayMap:
    """display_map lets the box SHOW a cleaned-up device name while the
    actual value passed to the callback (and used for the tooltip/selection)
    stays the real one -- the real name is what resolve_input_device_index/
    mixer.init(devicename=...) can actually match against a device."""

    def _fake_widget(self):
        from ui.clSettingsWidget import SettingsWidget
        fake = SettingsWidget.__new__(SettingsWidget)
        fake.ui_elements = {}
        return fake

    def test_combo_shows_display_text_but_callback_receives_actual_value(self, qapp):
        from ui.clSettingsWidget import SettingsWidget
        fake = self._fake_widget()
        actual_a = "Microphone (G435 Wireless Gaming Headset)"
        actual_b = "Microphone (Razer Seiren Mini)"
        display_map = {actual_a: "G435 Wireless Gaming Headset", actual_b: "Razer Seiren Mini"}
        received = []
        wrapper = SettingsWidget._create_dropdown(
            fake, "k", "Label", [actual_a, actual_b], actual_a, received.append, display_map=display_map
        )
        combo = fake.ui_elements["k"]

        assert combo.currentText() == "G435 Wireless Gaming Headset"
        assert combo.currentData() == actual_a

        combo.setCurrentIndex(1)
        assert received == [actual_b]

    def test_current_value_is_preselected_by_actual_name_not_display_text(self, qapp):
        from ui.clSettingsWidget import SettingsWidget
        fake = self._fake_widget()
        options = ["Microphone (Realtek Audio)", "Microphone (G435 Wireless Gaming Headset)"]
        display_map = {
            "Microphone (Realtek Audio)": "Microphone (Realtek Audio)",  # ambiguous, kept full
            "Microphone (G435 Wireless Gaming Headset)": "G435 Wireless Gaming Headset",
        }
        wrapper = SettingsWidget._create_dropdown(
            fake, "k", "Label", options, options[1], lambda v: None, display_map=display_map
        )
        combo = fake.ui_elements["k"]

        assert combo.currentData() == options[1]
        assert combo.currentText() == "G435 Wireless Gaming Headset"

    def test_elided_tooltip_shows_the_actual_value_not_display_text(self, qapp):
        from ui.clSettingsWidget import SettingsWidget
        fake = self._fake_widget()
        actual = "Microphone (G435 Wireless Gaming Headset)"
        display_map = {actual: "G435 Wireless Gaming Headset"}
        wrapper = SettingsWidget._create_dropdown(
            fake, "k", "Label", [actual], actual, lambda v: None, elide=True, display_map=display_map
        )
        combo = fake.ui_elements["k"]

        assert combo.toolTip() == actual
