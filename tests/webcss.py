"""The web stylesheet the way the server joins it (web/css/*.css), for the tests that read its rules."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "utils"))
from clWebCss import bundle  # noqa: E402


def web_css():
    return bundle(os.path.join(ROOT, "web"))
