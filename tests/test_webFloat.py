"""Pinning and unpinning a window must hand it over without a gap and without a shift. The behaviour needs a real Electron to see (see the
JARVIS_SHELL_FLOATS recipe in docs/web_ui_plan.md); these pin the protocol so a refactor cannot quietly drop a step."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


MAIN, PRELOAD, UI = read("shell", "main.js"), read("shell", "preload.js"), read("web", "js", "ui.js")
DASH, WIDGET = read("web", "js", "main.js"), read("web", "js", "widget-main.js")


def test_unpinning_keeps_the_dashboards_copy_until_the_floating_window_is_on_screen():
    unpin = UI[UI.index("function unpin("):UI.index("export const unit")]
    assert "closeWindow(" not in unpin.split("if (canFloat && SHELL.onFloatShown)")[0], "the copy must not be removed when the button is pressed"
    assert "SHELL.onFloatShown((id) => { if (open.has(id)) closeWindow(id); })" in UI
    assert '"shell:float-shown"' in MAIN and "floatReady" in PRELOAD


def test_a_floating_window_is_shown_only_once_its_page_says_it_is_built():
    assert 'ipcMain.on("shell:float-ready"' in MAIN and "showFloat(id)" in MAIN
    assert "ready-to-show" not in MAIN, "first paint is before the window's content exists"
    assert "iconsReady()" in WIDGET and "floatReady()" in WIDGET


def test_unpinning_sends_the_rectangle_in_page_coordinates_and_the_shell_places_it_on_the_screen():
    assert "relative: true" in UI
    assert "req.relative" in MAIN and "d.x + req.x" in MAIN


def test_pinning_back_opens_the_copy_where_the_window_floated_and_only_then_closes_the_floating_one():
    assert 'win.webContents.send("shell:docked", id, { x: b.x - d.x, y: b.y - d.y, width: b.width, height: b.height })' in MAIN
    assert 'ipcMain.on("shell:dock-ready"' in MAIN and "finishDock(" in MAIN
    assert "openWindow(w.def, rect" in DASH and "SHELL.dockReady(id)" in DASH
    assert "export function openWindow(def, at)" in UI and "if (at) persist(def.id, el);" in UI


def test_window_positions_and_sizes_are_whole_pixels():
    """A window at a fractional position next to its floating twin at whole pixels is a hair off, and blurred."""
    assert "Math.round(Math.max(0, Math.min(x, W - r.width)))" in UI
    assert 'width: Math.round(w) + "px", height: Math.round(hh) + "px"' in UI


def test_a_floating_window_is_created_invisible_and_only_raised_when_ready():
    """Windows animates a window that appears (a small zoom-in, "growth") and one that closes. The window is shown at once but fully
    transparent and click-through while its page loads, so the animation runs unseen; showFloat only raises the opacity, and closing
    drops it first."""
    assert "w.setOpacity(0); w.setIgnoreMouseEvents(true); w.showInactive();" in MAIN
    assert "w.setIgnoreMouseEvents(false); w.setOpacity(1);" in MAIN
    assert "w.setOpacity(0); } catch { /* gone */ } w.close();" in MAIN
