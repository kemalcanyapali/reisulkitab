"""Raw PCM capture with a live level meter.

Dictation records one source. A meeting records two of them at once, the
microphone and what comes out of the speakers, and for that it goes through
ffmpeg: one process reading both devices and merging them into the two channels
of a single stream, which is the only way the two stay aligned with each other
over an hour.

Which programs do the capturing is a property of the machine, not of the code
above: PulseAudio or PipeWire on Linux, AVFoundation through ffmpeg on macOS.
They are gathered into one group each near the bottom of this file, and a
chooser picks between them.
"""

import array
import collections
import contextlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import wave

from PyQt6.QtCore import QObject, pyqtSignal

from i18n import t

RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16
CHUNK_FRAMES = 1024
CHUNK_BYTES = CHUNK_FRAMES * SAMPLE_WIDTH * CHANNELS
CHUNK_LATENCY_MS = round(CHUNK_FRAMES / RATE * 1000)
MIN_FRAMES = int(RATE * 0.25)
_FinalizerThread = threading.Thread
@contextlib.contextmanager
def _windows_com():
    """Give the current capture thread its own COM apartment."""
    if os.name != "nt":
        yield
        return
    import ctypes

    ole32 = ctypes.windll.ole32
    result = ole32.CoInitializeEx(None, 0)
    initialized = result in (0, 1)
    if not initialized and result & 0xFFFFFFFF != 0x80010106:
        raise OSError(result, "CoInitializeEx failed")
    try:
        yield
    finally:
        if initialized:
            ole32.CoUninitialize()


def _mono(frames, np):
    samples = np.asarray(frames)
    return samples if samples.ndim == 1 else samples.mean(axis=1)




class Recorder(QObject):
    """Runs the available sound-server recorder and reads raw PCM from stdout."""

    level = pyqtSignal(float)              # 0.0 - 1.0, for the waveform
    stopped = pyqtSignal(str, float, object)  # wav path, duration (s), per-chunk RMS
    failed = pyqtSignal(str)
    interrupted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._thread = None
        self._buffer = bytearray()
        self._rms = []
        self._cancelled = False
        self._stopping = False
        self._lock = threading.Lock()
        self._capture_stop = threading.Event()
        self._finalizing = False

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, target="", max_seconds=300):
        if self.active or self._finalizing:
            self.failed.emit(t("The previous recording is still stopping."))
            return False
        if sys.platform == "win32":
            return self._start_windows(target, max_seconds)
        cmd = recording_command(target)
        if not cmd:
            self.failed.emit(t(sound().missing))
            return False

        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except OSError as exc:
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False

        self._buffer = bytearray()
        self._rms = []
        self._cancelled = False
        self._stopping = False
        self._max_bytes = int(max_seconds * RATE * SAMPLE_WIDTH * CHANNELS)
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def _start_windows(self, target, max_seconds):
        try:
            mic, np = _windows_microphone(target), _numpy()
        except (ImportError, RuntimeError) as exc:
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False
        buffer = bytearray()
        rms_values = []
        capture_stop = threading.Event()
        self._buffer = buffer
        self._rms = rms_values
        self._cancelled = False
        self._stopping = False
        self._max_bytes = int(max_seconds * RATE * SAMPLE_WIDTH * CHANNELS)
        self._capture_stop = capture_stop
        try:
            thread = threading.Thread(
                target=self._pump_windows,
                args=(mic, np, capture_stop, buffer, rms_values, self._max_bytes),
                daemon=True,
            )
            self._thread = thread
            thread.start()
        except RuntimeError as exc:
            capture_stop.set()
            self._thread = None
            self._buffer = bytearray()
            self._rms = []
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False
        return True

    def _pump_windows(
            self, mic, np, capture_stop, buffer, rms_values, max_bytes):
        try:
            with _windows_com(), \
                    mic.recorder(samplerate=RATE,
                                 blocksize=CHUNK_FRAMES) as recorder:
                while not capture_stop.is_set():
                    frames = recorder.record(numframes=CHUNK_FRAMES)
                    if capture_stop.is_set():
                        break
                    chunk = _pcm16(_mono(frames, np), np)
                    peak, rms = chunk_levels(chunk)
                    with self._lock:
                        if self._buffer is not buffer:
                            break
                        buffer.extend(chunk)
                        rms_values.append(rms)
                        too_long = len(buffer) >= max_bytes
                    self.level.emit(peak)
                    if too_long:
                        self._stopping = True
                        capture_stop.set()
        except Exception as exc:
            with self._lock:
                current = self._buffer is buffer
                captured = bool(buffer)
            if not capture_stop.is_set() and current:
                message = t(
                    "Audio recorder stopped unexpectedly: {error}", error=exc)
                (self.interrupted if captured else self.failed).emit(message)

    def _pump(self):
        proc = self._proc
        stdout = proc.stdout
        try:
            while True:
                chunk = stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                peak, rms = chunk_levels(chunk)
                with self._lock:
                    self._buffer.extend(chunk)
                    self._rms.append(rms)
                    too_long = len(self._buffer) >= self._max_bytes
                self.level.emit(peak)
                if too_long:
                    self._terminate()
                    break
        except (OSError, ValueError):
            pass
        # Nobody asked it to end and it captured nothing: the recorder is not
        # installed properly, or the device was refused. Said out loud here,
        # because stop() would otherwise report it as a recording that was too
        # short, which sends the user looking in the wrong place.
        with self._lock:
            captured = bool(self._buffer)
        if self._stopping or self._cancelled or captured:
            return
        try:
            detail = proc.stderr.read().decode("utf-8", "replace").strip()
        except (AttributeError, OSError):
            detail = ""
        self.failed.emit(t(
            "Audio recorder stopped before receiving sound: {error}",
            error=detail or f"exit code {proc.returncode}",
        ))

    def _terminate(self):
        self._stopping = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=1.5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass

        self._capture_stop.set()
    def _finish_later(self, cancelled):
        if self._finalizing:
            return
        self._finalizing = True
        thread = self._thread

        def finish():
            try:
                while thread is not None and thread.is_alive():
                    thread.join(timeout=0.1)
                (self.cancel if cancelled else self.stop)()
            finally:
                self._finalizing = False

        _FinalizerThread(target=finish, daemon=True).start()

    def cancel(self):
        self._cancelled = True
        if sys.platform == "win32":
            self._stopping = True
            self._capture_stop.set()
        else:
            self._terminate()
        thread = self._thread
        if thread:
            thread.join(timeout=2)
            if thread.is_alive():
                self._finish_later(cancelled=True)
                return False
        self._thread = None
        self._proc = None
        with self._lock:
            self._buffer = bytearray()
            self._rms = []
        return True

    def stop(self):
        """End the recording and write the WAV file."""
        if sys.platform == "win32":
            if not self.active and not self._buffer:
                return
            self._stopping = True
            self._capture_stop.set()
        else:
            if not self._proc:
                return
            self._terminate()
        thread = self._thread
        if thread:
            thread.join(timeout=2)
            if thread.is_alive():
                self._finish_later(cancelled=False)
                return False
        self._thread = None
        self._proc = None
        with self._lock:
            pcm = bytes(self._buffer)
            rms = list(self._rms)
            self._buffer = bytearray()

        if self._cancelled:
            return

        frames = len(pcm) // (SAMPLE_WIDTH * CHANNELS)
        if frames < MIN_FRAMES:  # a stray keypress, not speech
            self.failed.emit(t("Recording too short, speak for at least 0.3 s"))
            return

        path = write_wav(pcm)
        self.stopped.emit(path, frames / RATE, rms)
        return True


def write_wav(pcm, rate=RATE, channels=CHANNELS, width=SAMPLE_WIDTH):
    fd, path = tempfile.mkstemp(prefix="reisulkuttab-", suffix=".wav")
    with open(fd, "wb") as raw, wave.open(raw, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return path


def recording_command(target=""):
    """A raw-s16 capture command for the sound system on this machine."""
    return sound().record(target)


def meeting_command(mic_target, system_target):
    """One ffmpeg reading both devices and merging them into two channels."""
    return sound().meeting(mic_target, system_target)


class MeetingRecorder(QObject):
    """Microphone and speaker output into one stereo file: left is you, right is
    everyone else.

    Who said what then needs no guessing at all, because the two voices never
    shared a channel to begin with. The recording is written to disk as it
    arrives rather than held in memory, so length is not a problem and a crash
    costs the tail of the meeting instead of all of it.
    """

    levels = pyqtSignal(float, float)      # mine, theirs
    stopped = pyqtSignal(str, float)       # wav path, duration (s)
    died = pyqtSignal()                    # ffmpeg quit on its own
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._thread = None
        self._wav = None
        self._log = None
        self._path = ""
        self._frames = 0
        self._cancelled = False
        self._stopping = False
        self._lock = threading.Lock()
        self._capture_stop = threading.Event()
        self._capture_error = ""
        self._finalizing = False

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, path, mic_target="", system_target="", max_seconds=14400):
        if self.active or self._finalizing:
            self.failed.emit(t("The previous meeting recording is still stopping."))
            return False
        if self._thread is not None:
            self._thread = None
            self._close_file()
            self._drop_log()
            if self._cancelled:
                try:
                    os.unlink(self._path)
                except OSError:
                    pass
        if sys.platform == "win32":
            return self._start_windows(
                path, mic_target, system_target, max_seconds)
        if not shutil.which("ffmpeg"):
            self.failed.emit(t("ffmpeg not found. Install it to record a meeting."))
            return False
        if not system_target:
            system_target = default_monitor()
        if not system_target:
            self.failed.emit(t("Could not work out which speaker output to record. "
                               "Pick one in Settings → Meeting."))
            return False

        cmd = meeting_command(mic_target, system_target)

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._wav = wave.open(path, "wb")
            self._wav.setnchannels(2)
            self._wav.setsampwidth(SAMPLE_WIDTH)
            self._wav.setframerate(RATE)
            # ffmpeg keeps talking to stderr for as long as it runs; a pipe
            # nobody drains would eventually block it, so it writes to a file.
            self._log = tempfile.TemporaryFile()
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=self._log, bufsize=0
            )
        except (OSError, wave.Error) as exc:
            self._close_file()
            self._drop_log()
            try:
                os.unlink(path)   # an empty header nobody will ever read
            except OSError:
                pass
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False

        self._path = path
        self._frames = 0
        self._cancelled = False
        self._stopping = False
        self._max_frames = int(max_seconds * RATE)
        self._capture_error = ""
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def _start_windows(self, path, mic_target, system_target, max_seconds):
        try:
            mic = _windows_microphone(mic_target)
            loopback = _windows_loopback(system_target)
            np = _numpy()
        except (ImportError, RuntimeError) as exc:
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._wav = wave.open(path, "wb")
            self._wav.setnchannels(2)
            self._wav.setsampwidth(SAMPLE_WIDTH)
            self._wav.setframerate(RATE)
        except (OSError, wave.Error) as exc:
            self._close_file()
            try:
                os.unlink(path)
            except OSError:
                pass
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False
        self._path = path
        self._frames = 0
        self._cancelled = False
        self._stopping = False
        self._max_frames = int(max_seconds * RATE)
        self._capture_error = ""
        capture_stop = threading.Event()
        self._capture_stop = capture_stop
        wav = self._wav
        try:
            thread = threading.Thread(
                target=self._pump_windows,
                args=(mic, loopback, np, capture_stop, wav),
                daemon=True,
            )
            self._thread = thread
            thread.start()
        except RuntimeError as exc:
            capture_stop.set()
            self._thread = None
            self._close_file()
            try:
                os.unlink(path)
            except OSError:
                pass
            self.failed.emit(t("Could not start recording: {error}", error=exc))
            return False
        return True

    def _pump_windows(self, mic, loopback, np, capture_stop, wav):
        try:
            with _windows_com(), \
                    mic.recorder(samplerate=RATE,
                                 blocksize=CHUNK_FRAMES) as mic_recorder, \
                    loopback.recorder(samplerate=RATE,
                                      blocksize=CHUNK_FRAMES) as loop_recorder:
                while not capture_stop.is_set():
                    mine = _mono(
                        mic_recorder.record(numframes=CHUNK_FRAMES), np)
                    if capture_stop.is_set():
                        break
                    theirs = _mono(
                        loop_recorder.record(numframes=CHUNK_FRAMES), np)
                    if capture_stop.is_set():
                        break
                    frames = min(len(mine), len(theirs))
                    stereo = np.column_stack((mine[:frames], theirs[:frames]))
                    chunk = _pcm16(stereo, np)
                    mine_level, their_level = stereo_levels(chunk)
                    with self._lock:
                        if self._wav is not wav:
                            break
                        wav.writeframes(chunk)
                        self._frames += frames
                        too_long = self._frames >= self._max_frames
                    self.levels.emit(mine_level, their_level)
                    if too_long:
                        self._stopping = True
                        capture_stop.set()
        except Exception as exc:
            if capture_stop.is_set():
                return
            with self._lock:
                current = self._wav is wav
                if current:
                    self._capture_error = str(exc)
            if current:
                self.died.emit()

    def _pump(self):
        stdout = self._proc.stdout
        block = CHUNK_FRAMES * SAMPLE_WIDTH * 2
        try:
            while True:
                chunk = stdout.read(block)
                if not chunk:
                    break
                mine, theirs = stereo_levels(chunk)
                with self._lock:
                    if self._wav is None:
                        break
                    self._wav.writeframes(chunk)
                    self._frames += len(chunk) // (SAMPLE_WIDTH * 2)
                    too_long = self._frames >= self._max_frames
                self.levels.emit(mine, theirs)
                if too_long:
                    self._terminate()
                    break
        except (OSError, ValueError, wave.Error):
            pass
        # Nobody asked it to end: the sound device went away, or ffmpeg fell
        # over. An hour into a meeting that has to be said out loud rather than
        # discovered afterwards.
        if not self._stopping:
            self.died.emit()

    def _terminate(self):
        self._stopping = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._capture_stop.set()

    def _close_file(self):
        with self._lock:
            wav, self._wav = self._wav, None
        if wav is not None:
            try:
                wav.close()
            except (OSError, wave.Error):
                pass

    def _error_tail(self):
        if self._log is None:
            return ""
        try:
            self._log.seek(0)
            text = self._log.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""
        lines = [line for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ""
    def _finish_later(self, cancelled):
        if self._finalizing:
            return
        self._finalizing = True
        thread = self._thread

        def finish():
            try:
                while thread is not None and thread.is_alive():
                    thread.join(timeout=0.1)
                (self.cancel if cancelled else self.stop)()
            finally:
                self._finalizing = False

        _FinalizerThread(target=finish, daemon=True).start()


    def _finish_process(self):
        self._terminate()
        thread = self._thread
        if thread:
            thread.join(timeout=3)
            if thread.is_alive():
                return None
        self._thread = None
        code = self._proc.poll() if self._proc else 0
        self._proc = None
        self._close_file()
        return code

    def cancel(self):
        self._cancelled = True
        if self._finish_process() is None:
            self._finish_later(cancelled=True)
            return False
        self._drop_log()
        try:
            os.unlink(self._path)
        except OSError:
            pass
        return True

    def stop(self):
        if sys.platform == "win32":
            if not self.active and self._wav is None:
                return
        elif not self._proc:
            return
        code = self._finish_process()
        if code is None:
            self._finish_later(cancelled=False)
            return False
        frames = self._frames
        if self._cancelled:
            self._drop_log()
            try:
                os.unlink(self._path)
            except OSError:
                pass
            return True

        # SIGINT is how the recording ends, and ffmpeg reports being interrupted
        # as a failure; only complain when nothing was captured either.
        if frames < MIN_FRAMES:
            tail = self._error_tail() or self._capture_error
            self._drop_log()
            try:
                os.unlink(self._path)
            except OSError:
                pass
            self.failed.emit(
                t("Nothing was recorded: {error}", error=tail or f"ffmpeg → {code}")
                if tail or code else t("Recording too short, speak for at least 0.3 s")
            )
            return
        self._drop_log()
        self.stopped.emit(self._path, frames / RATE)
        return True

    def _drop_log(self):
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None


def chunk_levels(chunk):
    """(peak, rms) in 0..1. Peak drives the waveform, RMS drives the silence check."""
    samples = array.array("h")
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return 0.0, 0.0
    samples.frombytes(chunk[:usable])
    peak = max(abs(min(samples)), abs(max(samples))) / 32768.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
    return min(1.0, peak), min(1.0, rms)


def stereo_levels(chunk):
    """(left peak, right peak) in 0..1 from interleaved stereo s16."""
    samples = array.array("h")
    usable = len(chunk) - (len(chunk) % 4)
    if usable <= 0:
        return 0.0, 0.0
    samples.frombytes(chunk[:usable])
    left, right = samples[0::2], samples[1::2]
    return _peak(left), _peak(right)


def _peak(samples):
    if not samples:
        return 0.0
    return min(1.0, max(abs(min(samples)), abs(max(samples))) / 32768.0)


# --- Windows WASAPI --------------------------------------------------------

def _soundcard():
    try:
        import soundcard
    except ImportError as exc:
        raise ImportError(
            "SoundCard is not installed; run: pip install -r requirements-windows.txt"
        ) from exc
    return soundcard


def _numpy():
    try:
        import numpy
    except ImportError as exc:
        raise ImportError(
            "NumPy is not installed; run: pip install -r requirements-windows.txt"
        ) from exc
    return numpy


def _pcm16(frames, np):
    """SoundCard float32 frames to little-endian signed 16-bit PCM."""
    clean = np.nan_to_num(frames, nan=0.0, posinf=1.0, neginf=-1.0)
    return (np.clip(clean, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _device(devices, target, kind):
    wanted = str(target or "").casefold()
    for device in devices:
        if wanted in (str(device.id).casefold(), str(device.name).casefold()):
            return device
    raise RuntimeError(f"{kind} not found: {target}")


def _windows_microphone(target=""):
    sc = _soundcard()
    if not target:
        mic = sc.default_microphone()
        if mic is None:
            raise RuntimeError("Windows has no default microphone")
        return mic
    return _device(sc.all_microphones(include_loopback=False), target, "Microphone")


def _windows_loopback(target=""):
    sc = _soundcard()
    if target:
        speaker = _device(sc.all_speakers(), target, "Speaker output")
    else:
        speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("Windows has no default speaker output")
    return sc.get_microphone(str(speaker.id), include_loopback=True)


def _windows_inputs():
    try:
        return [(str(device.id), str(device.name))
                for device in _soundcard().all_microphones(include_loopback=False)]
    except (ImportError, RuntimeError):
        return []


def _windows_outputs():
    try:
        return [(str(device.id), str(device.name))
                for device in _soundcard().all_speakers()]
    except (ImportError, RuntimeError):
        return []


def _windows_default_output():
    try:
        speaker = _soundcard().default_speaker()
        return str(speaker.id) if speaker else ""
    except (ImportError, RuntimeError):
        return ""


# --- the sound system, one group per machine -------------------------------

# Both meeting commands merge the same way: each input down to mono at our own
# rate, then the two of them into the left and right of one stream.
MERGE_FILTER = (
    f"[0:a]aresample={RATE}:async=1,aformat=sample_fmts=s16:channel_layouts=mono[m];"
    f"[1:a]aresample={RATE}:async=1,aformat=sample_fmts=s16:channel_layouts=mono[s];"
    "[m][s]amerge=inputs=2[out]"
)


def _pulse_record(target):
    """parec, or pw-record where PulseAudio's tools were left out.

    parec works with both PulseAudio and PipeWire's PulseAudio compatibility
    service, and its source names are the same ones shown by list_sources().
    Keep pw-record as the fallback for minimal native-PipeWire installations.
    """
    if shutil.which("parec"):
        cmd = [
            "parec", "--record", "--raw", f"--rate={RATE}",
            f"--channels={CHANNELS}", "--format=s16le",
            # Left alone, parec holds about two seconds before handing anything
            # over, and then hands over all of it at once: the level meter sits
            # still and jumps, and the tail of a recording can be lost on the
            # way out. A chunk of the meter is the unit the rest of this file
            # is measured in, so ask for that.
            f"--latency-msec={CHUNK_LATENCY_MS}",
        ]
        if target:
            cmd.append(f"--device={target}")
        return cmd
    if shutil.which("pw-record"):
        cmd = [
            "pw-record", *_pw_record_raw_option(), f"--rate={RATE}",
            f"--channels={CHANNELS}", "--format=s16",
        ]
        if target:
            cmd.append(f"--target={target}")
        cmd.append("-")
        return cmd
    return []


def _pw_record_raw_option():
    """Use --raw only on pw-record releases that provide it.

    PipeWire gained --raw in 1.4, and in the same release stopped treating a
    filename of "-" as raw on its own: before it, the option is refused and the
    recorder dies before any sound arrives; after it, leaving the option out
    wraps the stream in a container the rest of this file would read as noise.
    Ubuntu 24.04 and anything else still on 1.0 or 1.2 sit on the near side of
    that line, so ask the installed binary which form it understands.
    """
    try:
        result = subprocess.run(
            ["pw-record", "--help"], capture_output=True, text=True, timeout=2
        )
        help_text = (result.stdout or "") + (result.stderr or "")
    except (subprocess.SubprocessError, OSError):
        return ["--raw"]  # preserve the existing command when probing itself fails
    if not help_text.strip():
        return ["--raw"]
    return ["--raw"] if "--raw" in help_text else []


def _pulse_meeting(mic_target, system_target):
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "pulse", "-thread_queue_size", "4096", "-i", mic_target or "default",
        "-f", "pulse", "-thread_queue_size", "4096", "-i", system_target,
        "-filter_complex", MERGE_FILTER, "-map", "[out]",
        "-f", "s16le", "-ar", str(RATE), "-",
    ]


def _pactl_sources():
    if not shutil.which("pactl"):
        return []
    try:
        out = subprocess.run(
            ["pactl", "-f", "json", "list", "sources"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return []


def _pulse_inputs():
    return [
        (src.get("name", ""), src.get("description") or src.get("name", ""))
        for src in _pactl_sources()
        if not src.get("name", "").endswith(".monitor")
    ]


def _pulse_outputs():
    return [
        (src.get("name", ""), src.get("description") or src.get("name", ""))
        for src in _pactl_sources()
        if src.get("name", "").endswith(".monitor")
    ]


def _pulse_default_output():
    if not shutil.which("pactl"):
        return ""
    try:
        sink = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""
    if not sink:
        return ""
    monitor = f"{sink}.monitor"
    names = {name for name, _ in _pulse_outputs()}
    return monitor if not names or monitor in names else ""


# macOS hands out no monitor of its own: what the speakers are playing is not
# an input, and the only way to record it is a driver that pretends to be one.
# These are the three people install.
LOOPBACK_DEVICES = ("blackhole", "loopback", "soundflower")


def _avfoundation_record(target):
    if not shutil.which("ffmpeg"):
        return []
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        # AVFoundation names an input "video:audio", so the empty half in front
        # of the colon is what says this recording has no picture in it.
        "-f", "avfoundation", "-i", f":{target or 'default'}",
        "-ac", str(CHANNELS), "-ar", str(RATE), "-f", "s16le", "-",
    ]


def _avfoundation_meeting(mic_target, system_target):
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-thread_queue_size", "4096",
        "-f", "avfoundation", "-i", f":{mic_target or 'default'}",
        "-thread_queue_size", "4096",
        "-f", "avfoundation", "-i", f":{system_target}",
        "-filter_complex", MERGE_FILTER, "-map", "[out]",
        "-f", "s16le", "-ar", str(RATE), "-",
    ]


def _avfoundation_inputs():
    """[(index, name)] for every capture device AVFoundation offers.

    The index is what the recorder is given, because that is what ffmpeg takes;
    it changes when devices are plugged in, which is why the name is shown.
    """
    if not shutil.which("ffmpeg"):
        return []
    try:
        # Listing devices is not a thing ffmpeg can do without an input, so it
        # is asked for one it cannot open: the list comes out on stderr and the
        # command then fails, which is the documented way of doing this.
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation",
             "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return []

    devices, listing = [], False
    for line in result.stderr.splitlines():
        if "AVFoundation audio devices:" in line:
            listing = True
            continue
        if not listing:
            continue
        match = re.search(r"\[(\d+)\]\s+(.+)$", line)
        if match:
            devices.append((match.group(1), match.group(2).strip()))
    return devices


def _avfoundation_default_output():
    for name, description in _avfoundation_inputs():
        if any(word in description.lower() for word in LOOPBACK_DEVICES):
            return name
    return ""


Sound = collections.namedtuple(
    "Sound",
    # How to capture one source and two at once, the two device lists, which
    # device a meeting records the far side from, and what to say when the
    # programs for any of it are not installed.
    "record meeting inputs outputs default_output missing",
)

PULSE = Sound(
    record=_pulse_record,
    meeting=_pulse_meeting,
    inputs=_pulse_inputs,
    outputs=_pulse_outputs,
    default_output=_pulse_default_output,
    missing="No audio recorder found. Install pulseaudio-utils or pipewire-audio.",
)

COREAUDIO = Sound(
    record=_avfoundation_record,
    meeting=_avfoundation_meeting,
    inputs=_avfoundation_inputs,
    # Every macOS capture device is offered as the far side of a meeting, the
    # loopback driver among them: there is no way to tell them apart, and an
    # empty list would leave nothing to pick.
    outputs=_avfoundation_inputs,
    default_output=_avfoundation_default_output,
    missing="ffmpeg not found. Install it with: brew install ffmpeg",
)


def sound():
    """The process-backed recorder for Linux or macOS."""
    return COREAUDIO if sys.platform == "darwin" else PULSE


def list_sources():
    """[(name, description)] for every real input source."""
    return _windows_inputs() if sys.platform == "win32" else sound().inputs()


def list_monitors():
    """[(name, description)] for recordable speaker outputs."""
    return _windows_outputs() if sys.platform == "win32" else sound().outputs()


def default_monitor():
    """The device the far side of a meeting comes from, or ''."""
    return (_windows_default_output() if sys.platform == "win32"
            else sound().default_output())
