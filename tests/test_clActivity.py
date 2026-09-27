"""Tests for the dashboard's user-activity reporter."""
import os
import sys

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from ui import clActivity


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def reporter(qapp, monkeypatch):
    published = []
    monkeypatch.setattr(clActivity.ActivityReporter, "_publish", staticmethod(lambda now: published.append(now)))

    class Inline:
        def __init__(self, target, args=(), daemon=None):
            self.run = lambda: target(*args)

        def start(self):
            self.run()
    monkeypatch.setattr(clActivity.threading, "Thread", Inline)
    clock = Clock()
    r = clActivity.ActivityReporter(clock=clock)
    r.published, r.clock = published, clock
    return r


def key():
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a")


def click():
    return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


class TestActivityReporter:
    def test_key_presses_and_clicks_are_reported_but_never_swallowed(self, reporter):
        assert reporter.eventFilter(None, key()) is False
        reporter.clock.t += 10
        assert reporter.eventFilter(None, click()) is False
        assert reporter.published == [100.0, 110.0]

    def test_reports_are_rate_limited(self, reporter):
        for _ in range(5):
            reporter.eventFilter(None, key())
            reporter.clock.t += 1
        assert len(reporter.published) == 1
        reporter.clock.t += 10
        reporter.eventFilter(None, key())
        assert len(reporter.published) == 2

    def test_focus_changes_do_not_count(self, reporter):
        reporter.eventFilter(None, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.ActiveWindowFocusReason))
        assert reporter.published == []
