// Orb state machine, ported from clUI.MqttThread.evaluate_state: speaking beats recording beats listening beats
// processing beats attention beats idle.
import { bridge } from "./bridge.js";

export const brain = { state: "IDLE", vol: 0, options: null, listeners: new Set() };
const f = { tts: false, mic: "IDLE", processing: false, attention: false };

function evaluate() {
  const s = f.tts ? "SPEAKING" : f.mic === "RECORDING" ? "RECORDING" : f.mic === "LISTENING" ? "LISTENING"
    : f.processing ? "PROCESSING" : f.attention ? "ATTENTION" : "IDLE";
  if (s !== brain.state) { brain.state = s; brain.listeners.forEach(fn => fn(s)); }
}

bridge.on("jarvis/sys/tts_state", p => {
  if (p.state === "active") { f.tts = true; f.processing = false; }
  else if (p.state === "idle") f.tts = false;
  evaluate();
});
bridge.on("jarvis/sys/mic_control", p => {
  const a = p.action;
  if (a === "attention_on") f.attention = true;
  else if (a === "request_reply" || a === "open_window") { f.mic = "LISTENING"; f.processing = false; }
  else if (a === "attention_off") { f.attention = false; f.mic = "IDLE"; f.processing = false; }
  else if (a === "cancel") { f.mic = "IDLE"; f.processing = false; }
  evaluate();
});
bridge.on("jarvis/sys/mic_state", p => {
  const s = p.state;
  if (s === "recording") f.mic = "RECORDING";
  else if (s === "listening") f.mic = "LISTENING";
  else if (s === "processing") { f.processing = true; f.mic = "IDLE"; }
  else if (s === "idle") { f.mic = "IDLE"; f.processing = false; }
  evaluate();
});
bridge.on("jarvis/sys/audio_process", p => {
  const s = p.state || "active";
  if (s === "active") { f.processing = true; f.mic = "IDLE"; }
  else if (s === "idle") { f.processing = false; if (f.mic === "LISTENING" && !f.attention) f.mic = "IDLE"; }
  evaluate();
});
const vol = p => { if (p && typeof p.rms === "number") brain.vol = p.rms; };
bridge.on("jarvis/sys/audio_vol", vol);
bridge.on("jarvis/sys/volume", vol);
