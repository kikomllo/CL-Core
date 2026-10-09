import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

FONTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets', 'fonts'))


_APP = None


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv)   # held at module level: a collected QApplication can't be rebuilt
    return _APP


class TestBundledFont:
    def test_every_font_file_the_theme_lists_is_bundled(self):
        from clTheme import Theme
        for filename in Theme.FONT_FILES:
            assert os.path.exists(os.path.join(FONTS_DIR, filename)), filename

    def test_the_stylesheet_family_leads_with_the_bundled_font(self):
        from clTheme import Theme
        assert Theme.FONT_FAMILY.startswith(f"'{Theme.FONT_NAME}'")
        assert Theme.FONT_NAME == "JetBrains Mono"

    def test_loading_registers_the_regular_bold_extrabold_and_italic_styles_under_one_family(self, qapp):
        from PyQt6.QtGui import QFontDatabase
        import clUI
        from clTheme import Theme

        clUI._load_bundled_fonts()

        styles = QFontDatabase.styles(Theme.FONT_NAME)
        assert Theme.FONT_NAME in QFontDatabase.families()
        for style in ("Regular", "Bold", "ExtraBold", "Italic", "Bold Italic"):
            assert style in styles, (style, styles)

    def test_clock_font_resolves_to_the_bundled_family(self, qapp):
        from PyQt6.QtGui import QFontInfo
        import clUI
        from clTheme import Theme
        from ui.clClockDisplay import ClockDisplay

        clUI._load_bundled_fonts()

        assert QFontInfo(ClockDisplay._font(20)).family() == Theme.FONT_NAME
