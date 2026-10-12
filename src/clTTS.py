import asyncio
import logging
import json
import os
import uuid
import hashlib
import random
import time
import threading
import wave
import base64
import edge_tts
import av
import numpy as np
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
from pygame import mixer
import aiomqtt
from typing import Dict, Any
import sys

from utils.clConfigLoader import ConfigLoader

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('TTS')

import queue
from utils.clEcho import StreamResampler24to16
from utils.clTTSEngines import PiperEngine, resample

# Speech is local (settings.tts_engine):
#   "piper" - (default) Piper's ARU voice tuned toward Ryan: ~300MB while speaking, freed ~30s after use
#   "edge"  - cloud edge-tts first (Microsoft began 403-ing its handshake on 2026-10-10), Piper when it fails
MIXER_RATE = 24000           # the mixer's rate; engine audio is resampled to it once per sentence
SPEECH_CHANNEL = 1           # mixer.Channel for streamed speech (mixer.music plays cached phrase files)
BLIP_CHANNEL = 2             # the "I heard you" chirp before every reply
PREBUFFER_SECONDS = 0.25     # audio generated before streamed speech starts, against underruns
CACHE_MAX_CHARS = 140        # only short, likely-to-repeat phrases are cached (AI replies are unique)
EDGE_BLOCK_SECONDS = 120.0   # after edge-tts fails, skip it this long (no doomed retries per phrase)
PRELOAD_WAIT_SECONDS = 45.0  # how long a reply spoken at startup waits for the primary voice to load
VIZ_FRAME = 0.024            # seconds per audio_vol sample
REF_CHUNK = 4800             # 16kHz samples (0.3s) per echo-reference message


def _init_mixer(device_name=None) -> None:
    kwargs = dict(frequency=MIXER_RATE, size=-16, channels=1, buffer=1024)
    if device_name and device_name != "System Default":
        kwargs["devicename"] = device_name
    mixer.init(**kwargs)


class TTSManager:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.assets_dir = os.path.abspath(os.path.join(self.base_dir, "..", "assets"))
        self.blip_path = os.path.join(self.assets_dir, "blip.mp3")
        self.semaphore = asyncio.Semaphore(1)
        self.abort_counter = 0
        self.edge_blocked_until = 0.0
        self.piper = PiperEngine()
        self.preloading = False
        self._warming = False
        self.gain = 1.0             # JARVIS's own voice level; the mic lowers it if the speaker is too loud to interrupt
        self.publish_ref = True
        self._blip_audio = None
        self._blip = None
        self._blip_loaded = False
        self._stop_event = threading.Event()

        # Load the dynamic responses once at boot
        responses_data = ConfigLoader().load_json("responses.json")
        self.tts_responses = responses_data.get("mqtt", {})

        core_data = ConfigLoader().load_json("core.json")
        self.silent_mode = core_data.get("settings", {}).get("silent_mode", False)
        self.tts_engine = core_data.get("settings", {}).get("tts_engine", "piper")
        # Piper is cheap to reload (~2s, hidden behind the user speaking), so it is released soon after use.
        self.piper_idle_seconds = float(core_data.get("settings", {}).get("tts_piper_idle_seconds", 30))
        barge_cfg = core_data.get("settings", {}).get("barge_in", {})
        self.publish_ref = bool(barge_cfg.get("enabled", True))
        self.state_path = os.path.join(self.base_dir, "..", "data", "barge_in_state.json")
        self.gain = self._load_gain()
        output_device = core_data.get("settings", {}).get("audio_settings", {}).get("output_device")

        try:
            _init_mixer(output_device)
        except Exception as e:
            logging.error(f"Failed to init mixer on '{output_device}', using system default: {e}")
            _init_mixer()
        logging.info("Audio mixer initialized.")
        self.clean_old_cache(max_files=200)

    def clean_old_cache(self, max_files: int = 30) -> None:
        """Removes oldest cached tts_*.mp3 files to prevent assets folder bloat."""
        try:
            tts_files = []
            for f in os.listdir(self.assets_dir):
                if f.startswith("tts_") and f.endswith((".mp3", "_local.wav")):
                    full_path = os.path.join(self.assets_dir, f)
                    tts_files.append((full_path, os.path.getmtime(full_path)))
            
            if len(tts_files) > max_files:
                tts_files.sort(key=lambda x: x[1])
                files_to_remove = tts_files[:-max_files]
                for path, _ in files_to_remove:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                logging.info(f"Cleaned up {len(files_to_remove)} old cached TTS files.")
        except Exception as e:
            logging.error(f"Failed to clean TTS cache: {e}")

    def get_audio_rms(self, file_path, suppress_log=False):
        try:
            container = av.open(file_path)
            rms_list = []
            for frame in container.decode(audio=0):
                arr = frame.to_ndarray()
                if arr.size > 0:
                    arr_f = arr.astype(np.float32)
                    if arr.dtype == np.int16:
                        arr_f /= 32768.0
                    elif arr.dtype == np.int32:
                        arr_f /= 2147483648.0
                    rms_list.append(float(np.sqrt(np.mean(arr_f**2))))
                else:
                    rms_list.append(0.0)
            return rms_list
        except Exception as e:
            if not suppress_log:
                logging.error(f"Failed to extract RMS: {e}")
            return []

    def get_wav_rms(self, file_path):
        """Per-24ms RMS of a WAV, matching the frame size generate_and_play assumes for mp3."""
        try:
            with wave.open(file_path, "rb") as wf:
                window = max(1, int(wf.getframerate() * 0.024))
                samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            return [float(np.sqrt(np.mean(samples[i:i + window] ** 2))) for i in range(0, len(samples), window)]
        except Exception as e:
            logging.error(f"Failed to extract WAV RMS: {e}")
            return []

    async def _synthesize_edge(self, text, voice, path) -> bool:
        """edge-tts with one retry for a transient blip; True when `path` was written."""
        for attempt in range(2):
            try:
                await edge_tts.Communicate(text, voice, rate="+27%", pitch="-5Hz").save(path)
                return True
            except Exception as e:
                logging.error(f"[TTS] edge-tts request failed (attempt {attempt + 1}/2): {e}")
                try: os.remove(path)  # save() may leave an empty file behind
                except OSError: pass
                if attempt == 0:
                    await asyncio.sleep(0.5)
        return False

    # ------------------------------------------------------------------
    # Engine selection
    # ------------------------------------------------------------------
    def _engine_order(self):
        return [self.piper]

    def _primary_engine_name(self) -> str:
        return "piper"

    async def _await_preload(self) -> None:
        """A reply spoken during startup (the boot announcement) waits for the voice to finish loading."""
        deadline = time.time() + PRELOAD_WAIT_SECONDS
        while self.preloading and time.time() < deadline:
            await asyncio.sleep(0.1)

    def _cache_tag(self) -> str:
        """Which voice a cached phrase was spoken in, so switching engines never replays the wrong one."""
        return f"piper:{self.piper.voice_tag}"

    # ------------------------------------------------------------------
    # Level + echo reference (so the mic can hear the user over JARVIS)
    # ------------------------------------------------------------------
    def _load_gain(self) -> float:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return min(1.0, max(0.05, float(json.load(f).get("gain", 1.0))))
        except Exception:
            return 1.0

    def set_gain(self, gain: float) -> None:
        self.gain = min(1.0, max(0.05, float(gain)))
        logging.info(f"[TTS] Voice level set to {self.gain:.2f} (keeps interrupting possible on loud speakers).")

    async def _send_ref(self, client, t0: float, offset: int, ref16: np.ndarray) -> None:
        """Tell the mic what is being played (16kHz int16, positioned by wall-clock `t0` + `offset` samples)."""
        if client is None or not self.publish_ref or len(ref16) == 0:
            return
        for i in range(0, len(ref16), REF_CHUNK):
            part = ref16[i:i + REF_CHUNK]
            await client.publish("jarvis/sys/tts_ref", json.dumps({
                "t0": t0, "off": int(offset + i), "pcm": base64.b64encode(part.tobytes()).decode("ascii")}))

    @staticmethod
    def _to_ref16(audio16: np.ndarray) -> np.ndarray:
        return (np.clip(audio16, -1.0, 1.0) * 32767).astype(np.int16)

    # ------------------------------------------------------------------
    # The blip
    # ------------------------------------------------------------------
    def _load_blip_audio(self):
        """blip.mp3 as mono float32 at MIXER_RATE with its silent lead-in cut. The file is 0.96s but
        its first ~0.6s is silence, which delayed every reply by that much before the chirp."""
        try:
            import av
            with av.open(self.blip_path) as container:
                parts, rate = [], None
                for frame in container.decode(audio=0):
                    a = frame.to_ndarray().astype(np.float32)
                    parts.append(a.mean(axis=0) if a.ndim > 1 else a)
                    rate = frame.sample_rate
            audio = np.concatenate(parts)
            if audio.dtype != np.float32 or np.abs(audio).max() > 2.0:
                audio = audio / 32768.0
            loud = np.where(np.abs(audio) > 0.02 * max(float(np.abs(audio).max()), 1e-9))[0]
            if len(loud) == 0:
                return None
            audio = audio[max(0, int(loud[0]) - int(rate * 0.01)):]
            return resample(audio, rate, MIXER_RATE)
        except Exception as e:
            logging.warning(f"[TTS] Could not prepare the blip ({e}); playing the file as is.")
            return None

    def _blip_sound(self):
        if not self._blip_loaded:
            self._blip_loaded = True
            audio = self._load_blip_audio() if os.path.exists(self.blip_path) else None
            self._blip_audio = audio
            self._blip = self._to_sound(audio) if audio is not None else None
        return self._blip

    async def _play_blip(self, client=None) -> None:
        sound = self._blip_sound()
        if sound is not None:
            channel = mixer.Channel(BLIP_CHANNEL)
            sound.set_volume(self.gain)
            t0 = time.time()
            channel.play(sound)
            if self._blip_audio is not None:
                await self._send_ref(client, t0, 0, self._to_ref16(StreamResampler24to16().process(self._blip_audio * self.gain)))
            while channel.get_busy():
                await asyncio.sleep(0.01)
        elif os.path.exists(self.blip_path):          # could not be trimmed: the old way
            mixer.music.load(self.blip_path)
            mixer.music.play()
            while mixer.music.get_busy():
                await asyncio.sleep(0.02)
            mixer.music.unload()

    async def warm(self) -> None:
        """Start the voice if it is not loaded. Called when the user begins speaking, so the load overlaps
        with their command and the speech recognition instead of delaying the reply."""
        engine = self.piper
        if engine.ready() or self._warming or self.preloading:
            return
        self._warming = True
        try:
            t = time.time()
            await asyncio.to_thread(engine.load)
            logging.info(f"[TTS] {engine.name} voice started for the incoming reply ({time.time() - t:.1f}s).")
        except Exception as e:
            logging.error(f"[TTS] Could not start the {engine.name} voice: {e}")
        finally:
            self._warming = False

    def handle_mic_state(self, payload: dict) -> None:
        if payload.get("state") in ("listening", "recording"):
            asyncio.create_task(self.warm())

    def reap_idle(self) -> bool:
        """Frees the Piper voice (~100MB) once it has been unused for tts_piper_idle_seconds. Never while a
        reply is being spoken or started."""
        if self.semaphore.locked() or self._warming or self.preloading:
            return False
        if self.piper_idle_seconds > 0 and self.piper.unload_if_idle(self.piper_idle_seconds):
            logging.info(f"[TTS] Piper voice unloaded after {self.piper_idle_seconds:.0f} idle seconds (frees the RAM).")
            return True
        return False

    def close(self) -> None:
        try: self.piper.shutdown()
        except Exception: pass

    async def preload(self) -> None:
        """Load the voice at startup so the boot announcement doesn't pay for it."""
        engine = self.piper
        t = time.time()
        self.preloading = True
        try:
            await asyncio.to_thread(engine.load)
            logging.info(f"[TTS] {engine.name} engine ready in {time.time() - t:.1f}s.")
        except Exception as e:
            logging.error(f"[TTS] Could not load the {engine.name} engine: {e}")
        finally:
            self.preloading = False

    def stop_speaking(self) -> None:
        """Cuts off any streamed speech now (generation and playback)."""
        self._stop_event.set()
        try: mixer.Channel(SPEECH_CHANNEL).stop()
        except Exception: pass

    # ------------------------------------------------------------------
    # Streaming synthesis + playback
    # ------------------------------------------------------------------
    @staticmethod
    def _viz_frames(audio: np.ndarray, carry: np.ndarray):
        """RMS (0-100) per 24ms window of `audio`, carrying the partial window to the next call."""
        win = int(MIXER_RATE * VIZ_FRAME)
        buf = np.concatenate([carry, audio]) if len(carry) else audio
        n = len(buf) // win
        frames = [min(100, int(float(np.sqrt(np.mean(buf[i * win:(i + 1) * win] ** 2))) * 500)) for i in range(n)]
        return frames, buf[n * win:]

    @staticmethod
    def _to_sound(audio: np.ndarray):
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        channels = (mixer.get_init() or (MIXER_RATE, -16, 2))[2]
        if channels > 1:                       # SDL reports a stereo mixer even when asked for mono
            pcm = np.repeat(pcm[:, None], channels, axis=1).reshape(-1)
        return mixer.Sound(buffer=pcm.tobytes())

    def _start_producer(self, engine, text, stop, out):
        """Runs engine.stream() in a thread, pushing resampled chunks (then None, or an Exception) to `out`."""
        stats = {"t_first": None, "t_start": None, "t_end": None}

        def run():
            try:
                engine.load()                      # model load must not count towards the speed measurement
                stats["t_start"] = time.time()
                for chunk in engine.stream(text, stop):
                    if stats["t_first"] is None:
                        stats["t_first"] = time.time()
                    out.put(resample(chunk, engine.sample_rate, MIXER_RATE))
                stats["t_end"] = time.time()
                out.put(None)
            except Exception as e:
                out.put(e)

        threading.Thread(target=run, daemon=True, name=f"tts-{engine.name}").start()
        return stats

    async def _speak_streaming(self, client, text, cache_path, t_req, abort_count) -> bool:
        """Synthesizes `text` and plays it as it is generated. True if anything was spoken."""
        await self._await_preload()
        async with self.semaphore:
            if self.abort_counter != abort_count:
                logging.info("[TTS] Task aborted before playing.")
                return True
            try:
                if client:
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "active"}))

                for engine in self._engine_order():
                    outcome = await self._play_stream(client, engine, text, cache_path, t_req, abort_count)
                    if outcome != "failed":
                        return True
                    logging.warning(f"[TTS] {engine.name} produced no audio.")
                return False
            finally:
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))
                    await asyncio.sleep(0.05)

    async def _play_stream(self, client, engine, text, cache_path, t_req, abort_count) -> str:
        """One engine attempt: 'done', 'aborted', or 'failed' (nothing was spoken)."""
        loop = asyncio.get_event_loop()
        stop = self._stop_event = threading.Event()
        chunks: "queue.Queue" = queue.Queue()
        stats = self._start_producer(engine, text, stop, chunks)   # generating while the blip plays

        await self._play_blip(client)                 # ducking (triggered by tts_state active) runs meanwhile

        channel = mixer.Channel(SPEECH_CHANNEL)
        pending, spoken, viz, carry = [], [], [], np.zeros(0, dtype=np.float32)
        started, finished, error = False, False, None
        start_time, last_pub = 0.0, 0.0
        gain, ref_rs, ref_off, ref_t0 = self.gain, StreamResampler24to16(), 0, 0.0

        while True:
            if self.abort_counter != abort_count or stop.is_set():
                stop.set()
                channel.stop()
                return "aborted"

            try:
                while True:
                    item = chunks.get_nowait()
                    if item is None:
                        finished = True
                    elif isinstance(item, Exception):
                        finished, error = True, item
                    else:
                        pending.append(item)
                        spoken.append(item)
                        frames, carry = self._viz_frames(item, carry)
                        viz.extend(frames)
            except queue.Empty:
                pass

            pending_seconds = sum(len(p) for p in pending) / MIXER_RATE
            if pending and channel.get_queue() is None and (started or finished or pending_seconds >= PREBUFFER_SECONDS):
                block = np.concatenate(pending) * gain
                sound = self._to_sound(block)
                ref16 = self._to_ref16(ref_rs.process(block)) if (client and self.publish_ref) else None
                pending.clear()
                if not started:
                    ref_t0 = time.time()
                    channel.play(sound)
                    started, start_time = True, loop.time()
                    if ref16 is not None:
                        await self._send_ref(client, ref_t0, 0, ref16)
                        ref_off = len(ref16)
                    if client:
                        await client.publish("jarvis/sys/trace", json.dumps({
                            "t_tts_req": t_req, "t_synth_done": stats["t_first"] or time.time(),
                            "t_audio": time.time(), "cached": False
                        }))
                else:
                    if ref16 is not None:
                        await self._send_ref(client, ref_t0, ref_off, ref16)
                        ref_off += len(ref16)
                    channel.queue(sound)

            if started and client and loop.time() - last_pub >= 0.04:
                last_pub = loop.time()
                idx = int((loop.time() - start_time) / VIZ_FRAME)
                if idx < len(viz):
                    await client.publish("jarvis/sys/audio_vol", json.dumps({"rms": viz[idx]}))

            if finished and not pending:
                if not started:
                    if error:
                        logging.error(f"[TTS] {engine.name} failed: {error}")
                    return "failed"
                if not channel.get_busy():
                    break
            await asyncio.sleep(0.01)

        if error:
            logging.error(f"[TTS] {engine.name} stopped part-way: {error}")
        elif spoken:
            audio = np.concatenate(spoken)
            # Cache keys name the primary voice, so a phrase said by the fallback engine must not be stored.
            if len(text) <= CACHE_MAX_CHARS and engine.name == self._primary_engine_name():
                self._write_cache(cache_path, audio)
        return "done"

    @staticmethod
    def _write_cache(path: str, audio: np.ndarray) -> None:
        try:
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(path + ".part", "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(MIXER_RATE)
                wf.writeframes(pcm.tobytes())
            os.replace(path + ".part", path)
        except Exception as e:
            logging.warning(f"[TTS] Could not cache phrase: {e}")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def generate_and_play(self, client, text, voice="en-GB-RyanNeural", ignore_silent=False, abort_count=0) -> None:
        if self.silent_mode and not ignore_silent and not ("alarm" in text.lower()):
            logging.info("[TTS] Silent mode active. Skipping TTS playback.")
            return

        t_req = time.time()
        file_hash = hashlib.md5(f"{text}_{voice}".encode()).hexdigest()
        mp3_file = os.path.join(self.assets_dir, f"tts_{file_hash}.mp3")
        local_hash = hashlib.md5(f"{text}_{self._cache_tag()}".encode()).hexdigest()
        local_file = os.path.join(self.assets_dir, f"tts_{local_hash}_local.wav")

        def usable(path):                      # an empty file is a failed save, not a cached phrase
            return os.path.exists(path) and os.path.getsize(path) > 0

        # Cloud voice only in "edge" mode (cached mp3 first, then a live request).
        if self.tts_engine == "edge":
            if not usable(mp3_file) and time.time() >= self.edge_blocked_until:
                if not await self._synthesize_edge(text, voice, mp3_file):
                    self.edge_blocked_until = time.time() + EDGE_BLOCK_SECONDS
                    logging.warning(f"[TTS] edge-tts unavailable; using the local voice for the next {EDGE_BLOCK_SECONDS:.0f}s.")
            if usable(mp3_file):
                os.utime(mp3_file, None)
                await self._play_file(client, mp3_file, t_req, cached=True, abort_count=abort_count)
                return

        if usable(local_file):
            os.utime(local_file, None)
            await self._play_file(client, local_file, t_req, cached=True, abort_count=abort_count)
            return

        if not await self._speak_streaming(client, text, local_file, t_req, abort_count):
            # Nothing could speak. Still emit the tts_state:idle the Daemon waits on to reopen
            # the mic for a pending follow-up, so a failed synthesis can't strand it.
            if client:
                await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))
                await client.publish("jarvis/sys/tts_done", "1")
        self.clean_old_cache(max_files=200)

    def _file_ref16(self, path: str) -> np.ndarray:
        """A cached 24kHz wav as it will sound (gain applied), at 16kHz for the echo canceller."""
        try:
            with wave.open(path, "rb") as wf:
                audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            return self._to_ref16(StreamResampler24to16().process(audio * self.gain))
        except Exception:
            return np.zeros(0, dtype=np.int16)

    async def _play_file(self, client, temp_file, t_req, cached, abort_count) -> None:
        """Plays a finished audio file (cached phrase or edge-tts mp3) with the blip, ducking and volume feed."""
        t_synth_done = time.time()
        self.clean_old_cache(max_files=200)

        async with self.semaphore:
            if self.abort_counter != abort_count:
                logging.info("[TTS] Task aborted before playing.")
                return
                
            try:
                if client:
                    # Broadcast state to the Daemon so it can handle the ducking
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "active"}))

                await self._play_blip(client)   # the Daemon ducks Spotify while it plays

                loaded = False
                for _ in range(5):
                    try:
                        mixer.music.load(temp_file)
                        loaded = True
                        break
                    except Exception: await asyncio.sleep(0.2)
                
                if not loaded:
                    logging.warning(f"Mixer failed to load TTS audio. Deleting corrupted cache: {temp_file}")
                    try: os.remove(temp_file)
                    except Exception: pass
                    return
                
                rms_list = []
                for attempt in range(5):
                    rms_list = self.get_wav_rms(temp_file) if temp_file.endswith(".wav") \
                        else self.get_audio_rms(temp_file, suppress_log=(attempt < 4))
                    if rms_list:
                        break
                    await asyncio.sleep(0.2)
                    
                if not rms_list:
                    logging.warning(f"Failed to extract RMS. Deleting corrupted cache: {temp_file}")
                    try: os.remove(temp_file)
                    except Exception: pass
                    return
                frame_duration = 0.024
                
                # --- SILENCE TRUNCATION ALGORITHM ---
                scaled_rms = [0 if np.isnan(r) else min(100, int(r * 500)) for r in rms_list]
                last_valid_idx = 0
                for i in range(len(scaled_rms) - 1, -1, -1):
                    if scaled_rms[i] > 0:
                        last_valid_idx = i
                        break
                
                cutoff_time = (last_valid_idx + 4) * frame_duration
                # ------------------------------------

                mixer.music.set_volume(self.gain)
                t_play = time.time()
                mixer.music.play()
                start_time = asyncio.get_event_loop().time()
                if client:
                    await client.publish("jarvis/sys/trace", json.dumps({
                        "t_tts_req": t_req, "t_synth_done": t_synth_done,
                        "t_audio": time.time(), "cached": cached
                    }))
                    if temp_file.endswith(".wav"):
                        await self._send_ref(client, t_play, 0, self._file_ref16(temp_file))

                while mixer.music.get_busy():
                    elapsed = asyncio.get_event_loop().time() - start_time

                    if elapsed >= cutoff_time:
                        mixer.music.stop()
                        break
                        
                    frame_idx = int(elapsed / frame_duration)
                    if frame_idx < len(scaled_rms):
                        val = scaled_rms[frame_idx]
                        if client:
                            await client.publish("jarvis/sys/audio_vol", json.dumps({"rms": val}))
                            
                    await asyncio.sleep(0.04)
                
            finally:
                mixer.music.unload()
                
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")
                    # Broadcast idle so the Daemon knows it is safe to unduck (if mic isn't opening)
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))
                    await asyncio.sleep(0.05)

    async def set_output_device(self, device_name: str) -> None:
        async with self.semaphore:
            self.stop_speaking()
            self._blip, self._blip_audio, self._blip_loaded = None, None, False   # Sounds belong to the old mixer
            mixer.quit()
            try:
                _init_mixer(device_name)
                logging.info(f"[TTS] Output device switched to: {device_name or 'System Default'}")
            except Exception as e:
                logging.error(f"[TTS] Failed to switch to output device '{device_name}': {e}")
                _init_mixer()

    async def play_audio_file(self, client, file_path: str, abort_count=0) -> None:
        """Plays an existing audio file safely through the TTS queue to avoid ALSA locks."""
        if not os.path.exists(file_path):
            logging.error(f"[TTS] Audio file not found: {file_path}")
            return
            
        async with self.semaphore:
            if self.abort_counter != abort_count:
                logging.info("[TTS] Audio file playback aborted.")
                return
                
            try:
                if client:
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "active"}))
                    await asyncio.sleep(0.2)
                    
                for _ in range(5):
                    try:
                        mixer.music.load(file_path)
                        break
                    except Exception: await asyncio.sleep(0.2)
                    
                rms_list = self.get_audio_rms(file_path)
                
                try:
                    import av
                    with av.open(file_path) as container:
                        true_duration = float(container.duration) / av.time_base
                except Exception:
                    true_duration = len(rms_list) * 0.024
                    
                frame_duration = true_duration / max(1, len(rms_list))
                
                scaled_rms = [0 if np.isnan(r) else min(100, int(r * 500)) for r in rms_list]
                
                if file_path.endswith('.wav'):
                    cutoff_time = true_duration + 1.0 # Never truncate user WAV recordings
                else:
                    last_valid_idx = 0
                    for i in range(len(scaled_rms) - 1, -1, -1):
                        if scaled_rms[i] > 0:
                            last_valid_idx = i
                            break
                    cutoff_time = (last_valid_idx + 4) * frame_duration
                    if cutoff_time < 0.5: cutoff_time = 10.0 # fallback
                
                mixer.music.play()
                start_time = asyncio.get_event_loop().time()
                
                while mixer.music.get_busy():
                    await asyncio.sleep(0.01)
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= cutoff_time:
                        mixer.music.stop()
                        break
                        
                    frame_idx = int(elapsed / frame_duration)
                    if frame_idx < len(scaled_rms):
                        val = scaled_rms[frame_idx]
                        if client:
                            await client.publish("jarvis/sys/audio_vol", json.dumps({"rms": val}))
                            
                    await asyncio.sleep(0.04)
            finally:
                mixer.music.unload()
                if client:
                    await client.publish("jarvis/sys/tts_done", "1")
                    await client.publish("jarvis/sys/tts_state", json.dumps({"state": "idle"}))


    async def handle_tts_request(self, client, payload: dict, abort_count=0) -> None:
        """Formats dynamic text from templates and sends it to the voice generator."""
        target_topic = payload.get("target_topic", "")
        command = payload.get("command", {})
        action = command.get("action", "")
        
        response_key = action

        if target_topic == "home/room/desk_light/set":
            if "color" in command: response_key = "color"
            elif "lum" in command: response_key = "lum"
            elif "temp" in command: response_key = "temp"
            
        elif target_topic == "pc/spotify/control" and action == "play":
            if "track_name" in command and "artist_name" in command: response_key = "play_track_artist"
            elif "track_name" in command: response_key = "play_track"
            elif "artist_name" in command: response_key = "play_artist"
            elif "playlist_name" in command: response_key = "play_playlist"

        phrases = self.tts_responses.get(target_topic, {}).get(response_key, [])

        if phrases:
            raw_phrase = random.choice(phrases)
            try:
                phrase = raw_phrase.format(**command)
            except KeyError:
                phrase = raw_phrase 
                
            followup_text = payload.get("followup_text")
            if followup_text:
                phrase += f" {followup_text}"

            await self.generate_and_play(client, phrase, abort_count=abort_count)

async def run_tts_service():
    manager = TTSManager()
    asyncio.create_task(manager.preload())

    async def idle_reaper():
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(manager.reap_idle)
    asyncio.create_task(idle_reaper())
    import atexit
    atexit.register(manager.close)      # the worker also ends by itself if this process dies
    attempt = 0
    while True:
        try:
            async with aiomqtt.Client("localhost") as client:
                attempt = 0
                await client.subscribe("jarvis/sys/speak")
                await client.subscribe("jarvis/sys/tts_stop")
                await client.subscribe("jarvis/sys/abort")
                await client.subscribe("jarvis/sys/tts_request")
                await client.subscribe("jarvis/sys/silent_mode")
                await client.subscribe("jarvis/sys/play_audio")
                await client.subscribe("jarvis/sys/mic_state")
                await client.subscribe("jarvis/sys/tts_control")
                logging.info("TTS Microservice initialized. Listening on MQTT topics...")
                await client.publish("jarvis/sys/module_ready", json.dumps({"module": "tts"}))
                
                async for message in client.messages:
                    topic = message.topic.value

                    if topic == "jarvis/sys/mic_state":
                        try:
                            manager.handle_mic_state(json.loads(message.payload.decode('utf-8')))
                        except json.JSONDecodeError:
                            pass
                        continue

                    if topic in ["jarvis/sys/tts_stop", "jarvis/sys/abort"]:
                        manager.abort_counter += 1
                        mixer.music.stop()
                        manager.stop_speaking()
                        continue

                    if topic == "jarvis/sys/silent_mode":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            manager.silent_mode = payload.get("silent_mode", False)
                            logging.info(f"[TTS] Silent mode updated: {manager.silent_mode}")
                        except json.JSONDecodeError:
                            pass
                        continue
                    
                    if topic == "jarvis/sys/speak":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            asyncio.create_task(manager.generate_and_play(
                                client, 
                                payload.get("text", ""), 
                                ignore_silent=payload.get("ignore_silent", False),
                                abort_count=manager.abort_counter
                            ))
                        except json.JSONDecodeError:
                            logging.error("Received malformed TTS JSON.")
                            
                    elif topic == "jarvis/sys/tts_request":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            asyncio.create_task(manager.handle_tts_request(
                                client, 
                                payload,
                                abort_count=manager.abort_counter
                            ))
                        except json.JSONDecodeError:
                            logging.error("Malformed TTS request JSON.")
                            
                    elif topic == "jarvis/sys/tts_control":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            if payload.get("action") == "set_output_device":
                                asyncio.create_task(manager.set_output_device(payload.get("device_name")))
                            elif payload.get("action") == "set_gain":
                                manager.set_gain(payload.get("gain", 1.0))
                        except json.JSONDecodeError:
                            logging.error("Malformed tts_control JSON.")

                    elif topic == "jarvis/sys/play_audio":
                        try:
                            payload = json.loads(message.payload.decode('utf-8'))
                            audio_path = payload.get("path")
                            if audio_path:
                                asyncio.create_task(manager.play_audio_file(
                                    client, 
                                    audio_path,
                                    abort_count=manager.abort_counter
                                ))
                        except json.JSONDecodeError:
                            logging.error("Malformed play_audio JSON.")

        except aiomqtt.MqttError as e:
            delay = min(60, 2 ** attempt)
            logging.error(f"MQTT Connection Error: {e}. Retrying in {delay} seconds...")
            await asyncio.sleep(delay)
            attempt += 1
        except asyncio.CancelledError:
            logging.info("TTS service shutting down.")
            break
        except Exception as e:
            logging.error(f"Service Error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="JARVIS TTS microservice")
    parser.add_argument("-t", "--text", help="Generate and speak this text once, then exit (no MQTT needed)")
    args = parser.parse_args()

    if args.text:
        manager = TTSManager()
        try:
            asyncio.run(manager.generate_and_play(None, args.text, ignore_silent=True))
        finally:
            manager.close()
    else:
        asyncio.run(run_tts_service())