"""Background threads: the serial link to the microcontroller firmware and a ping recorder."""
from __future__ import annotations

import os
import queue
import re
import subprocess

import serial
from PySide6.QtCore import QThread, Signal
from serial.tools import list_ports

from .model import parse_trial_line


def available_ports() -> list[tuple[str, str]]:
    """(device, description) for every serial port, microcontroller ports first."""
    ports = [(p.device, p.description or "") for p in list_ports.comports()]
    ports.sort(key=lambda x: (0 if "arduino" in x[1].lower() or "usb serial" in x[1].lower() else 1, x[0]))
    return ports


class SerialLink(QThread):
    """Reads the firmware's serial output line by line and forwards commands to it.

    Opening the port resets an ATmega328 microcontroller, so the firmware header appears about
    two seconds after connecting; wait for `header_seen` before sending settings.
    """

    line_received = Signal(str)
    trial_received = Signal(object)      # model.Trial
    raw_received = Signal(int, int)      # A0, A1 readings in raw mode
    info_received = Signal(str)          # '# ...' lines from the firmware
    header_seen = Signal(str)            # first '# endoscope latency probe' line
    connected = Signal(bool)
    error = Signal(str)

    def __init__(self, port: str, baud: int = 115200):
        super().__init__()
        self.port, self.baud = port, baud
        self._cmds: queue.Queue[str] = queue.Queue()
        self._stop = False

    def send(self, text: str) -> None:
        self._cmds.put(text)

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: C901 - single loop is clearer here
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.2)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.error.emit(f"Could not open {self.port}: {exc}")
            self.connected.emit(False)
            return
        self.connected.emit(True)
        buf = b""
        header_done = False
        try:
            while not self._stop:
                try:
                    while True:
                        cmd = self._cmds.get_nowait()
                        ser.write(cmd.encode("ascii"))
                        ser.flush()
                except queue.Empty:
                    pass
                chunk = ser.read(256)
                if chunk:
                    buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("ascii", "replace").strip()
                    if not line:
                        continue
                    self.line_received.emit(line)
                    if line.startswith("raw,"):
                        p = line.split(",")
                        try:
                            self.raw_received.emit(int(p[1]), int(p[2]))
                        except (IndexError, ValueError):
                            pass
                    elif line.startswith("#"):
                        self.info_received.emit(line)
                        if not header_done and "latency probe" in line:
                            header_done = True
                            self.header_seen.emit(line)
                    else:
                        t = parse_trial_line(line)
                        if t is not None:
                            self.trial_received.emit(t)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.error.emit(f"Serial error on {self.port}: {exc}")
        finally:
            try:
                ser.close()
            except Exception:
                pass
            self.connected.emit(False)


_TIME_RE = re.compile(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


class PingWorker(QThread):
    """Runs the system ping for a fixed number of probes and streams the RTTs."""

    sample = Signal(int)
    done = Signal(list, str)             # rtts, raw output

    def __init__(self, host: str, count: int):
        super().__init__()
        self.host, self.count = host, max(1, int(count))
        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self) -> None:
        flag = "-n" if os.name == "nt" else "-c"
        cmd = ["ping", flag, str(self.count), self.host]
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        lines: list[str] = []
        rtts: list[int] = []
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                lines.append(line)
                m = _TIME_RE.search(line)
                if m:
                    v = int(round(float(m.group(1))))
                    rtts.append(v)
                    self.sample.emit(v)
            self._proc.wait()
        except Exception as exc:
            lines.append(f"ping failed: {exc}\n")
        self.done.emit(rtts, "".join(lines))
