// jarvis/sys/ui_control actions that open a dashboard window (tests/test_webUiControl.py runs this under node). "show the logs" and "show my
// todo list" (voice or the text bar) end up here. Mode switches (set_overlay / set_fullscreen) are handled by shell.js.
export const SHOW_ACTIONS = { show_logs: "widget_logs", show_todo: "widget_todo" };

/** The id of the window an action opens, or null if the action is not about opening a window. */
export const windowForAction = (action) => (Object.prototype.hasOwnProperty.call(SHOW_ACTIONS, action) ? SHOW_ACTIONS[action] : null);

/** How to show a window now: "open" it in the dashboard, or, while only the overlay is up (it draws no windows and takes no clicks), "float"
 *  it: a window of its own on top of other apps, like an unpinned Qt widget, without leaving the overlay. */
export const showPlan = (mode, canFloat) => (mode === "overlay" && canFloat ? "float" : "open");
