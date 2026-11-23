// The Presence tab's text, as the Qt Settings widget words it (SettingsWidget.presence_text, _fill_pairing_panel,
// _refresh_pairing_countdown). Pure; tested under node.

/** One line about the paired device from a `jarvis/sys/presence` payload. */
export function presenceText(p) {
  if (!p) return "Waiting for the presence monitor...";
  if (!p.paired) return "No device paired.";
  if (p.available === false) return `Paired - Bluetooth unavailable (${p.reason || "unknown"}).`;
  if (p.present) return "Paired - here" + (p.rssi != null ? ` (signal ${p.rssi} dBm).` : ".");
  return "Paired - away.";
}

/** The step-by-step instructions for an open pairing (a `jarvis/monitor/pairing` payload with state "waiting"). */
export function pairingSteps(pairing) {
  if (pairing && pairing.static) {
    return "1. In your advertiser app, add a Service Data advertisement.\n"
      + "2. Paste the Service UUID and the Service data below (hex).\n"
      + "3. Start advertising and stay close to this PC.\n"
      + "Pairing finishes by itself when JARVIS hears it.";
  }
  return "1. Open your JARVIS beacon app and choose Add device.\n"
    + "2. Type in the Pairing code below (it is not a UUID).\n"
    + "3. Keep the app running and stay close to this PC.\n"
    + "Using nRF Connect or another advertiser app? There is no service data in this mode: "
    + "cancel, tick Test mode and pair again.";
}

/** How a finished pairing attempt is reported ("" for a state with no message). */
export function pairingResult(payload) {
  return {
    paired: "Paired.", expired: "Pairing timed out.", cancelled: "Pairing cancelled.",
    failed: `Pairing failed: ${(payload && payload.reason) || "unknown reason"}`,
  }[payload && payload.state] || "";
}

/** "Expires in 4:07." from the pairing's expiry (epoch seconds) and now (epoch seconds); "" without an expiry. */
export function countdownText(expiresAt, nowSeconds) {
  if (expiresAt == null) return "";
  const left = Math.max(0, Math.floor(expiresAt - nowSeconds));
  return `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}.`;
}

/** Wraps `fn` so it runs only the first time it is called (a page session: the module lives until the page reloads). The monitor SPEAKS its
 *  answer to monitor.status, so asking for it must happen once, when the user actually opens the Presence tab, never when Settings is built. */
export function once(fn) {
  let done = false;
  return (...args) => { if (done) return undefined; done = true; return fn(...args); };
}
