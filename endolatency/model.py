"""Data model, file formats and statistics for endoscope latency runs.

A *run* is one continuous series of trials recorded from the microcontroller firmware.
On disk a run is three files with the same stem:
    <stem>.csv        the serial log exactly as received (comment lines kept)
    <stem>.json       run metadata (setup, application, scene, sensor placement, threshold, notes)
    <stem>.ping.txt   optional raw output of a ping batch recorded during the run
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import stats as sps

NAN = float("nan")
HEADER = "trial,baseA,baseB,litA,litB,riseA_ms,riseB_ms,fallA_ms,fallB_ms,linkRise_ms,linkFall_ms"

MEASURES = {
    "riseA_ms": "Sensor A, LED on (rise)",
    "fallA_ms": "Sensor A, LED off (fall)",
    "riseB_ms": "Sensor B, LED on (rise)",
    "fallB_ms": "Sensor B, LED off (fall)",
    "linkRise_ms": "B minus A, rise edges",
    "linkFall_ms": "B minus A, fall edges",
}
SETUPS = {
    "two-screen": "Two screens: sensor A on the mentee screen, sensor B on the mentor screen",
    "one-screen-a": "One screen: sensor A only",
    "one-screen-b": "One screen: sensor B only",
}
SCENES = {"dark": "Dark (endoscope light source off)", "lit": "Endoscope light source on"}
APPLICATIONS = ["Telementoring web app", "Windows Camera app", "Vendor viewer", "Other"]


def channels_for_setup(setup: str) -> str:
    """Firmware channel command for a setup: x = both, a = sensor A only, b = sensor B only."""
    return {"two-screen": "x", "one-screen-a": "a", "one-screen-b": "b"}.get(setup, "x")


def measures_for_setup(setup: str) -> list[str]:
    if setup == "one-screen-a":
        return ["riseA_ms", "fallA_ms"]
    if setup == "one-screen-b":
        return ["riseB_ms", "fallB_ms"]
    return list(MEASURES)


@dataclass
class Trial:
    trial: int
    baseA: float
    baseB: float
    litA: float
    litB: float
    riseA_ms: float
    riseB_ms: float
    fallA_ms: float
    fallB_ms: float
    linkRise_ms: float
    linkFall_ms: float

    def get(self, key: str) -> float:
        return getattr(self, key)


def parse_trial_line(line: str) -> Trial | None:
    """Parse one firmware CSV line.

    Accepts the 11-column both-edge format (nan = not detected) and the earlier
    8-column rise-only format (-1 = not detected).
    """
    parts = line.strip().split(",")
    if not parts or not parts[0].isdigit():
        return None
    try:
        v = [float(x) for x in parts]
    except ValueError:
        return None
    if len(v) == 11:
        return Trial(int(v[0]), *v[1:])
    if len(v) == 8:
        a = v[5] if v[5] >= 0 else NAN
        b = v[6] if v[6] >= 0 else NAN
        return Trial(int(v[0]), v[1], v[2], v[3], v[4], a, b, NAN, NAN, b - a, NAN)
    return None


def parse_csv_text(text: str) -> list[Trial]:
    """Trials after the last firmware header in a serial log (or all lines if there is no header)."""
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith("trial,")]
    body = lines[starts[-1] + 1:] if starts else lines
    out = []
    for l in body:
        t = parse_trial_line(l)
        if t is not None:
            out.append(t)
    return out


_PING_RE = re.compile(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


def parse_ping_text(text: str) -> list[int]:
    return [int(round(float(m))) for m in _PING_RE.findall(text)]


@dataclass
class RunMeta:
    label: str = ""
    setup: str = "two-screen"
    application: str = ""
    scene: str = "dark"
    sensorA: str = ""
    sensorB: str = ""
    threshold: int = 30
    channels: str = "x"
    notes: str = ""
    started: str = ""
    ended: str = ""
    ping_host: str = ""
    firmware: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_dict(cls, d: dict) -> "RunMeta":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Run:
    path: str
    meta: RunMeta
    trials: list[Trial] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    ping: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.meta.label or os.path.splitext(os.path.basename(self.path))[0]

    @property
    def stem(self) -> str:
        return os.path.splitext(self.path)[0]

    def values(self, key: str) -> np.ndarray:
        return np.array([t.get(key) for t in self.trials], float)

    def started_dt(self) -> dt.datetime | None:
        try:
            return dt.datetime.fromisoformat(self.meta.started)
        except ValueError:
            return None


def sidecar_paths(csv_path: str) -> tuple[str, str]:
    stem = os.path.splitext(csv_path)[0]
    return stem + ".json", stem + ".ping.txt"


def load_run(csv_path: str) -> Run:
    with open(csv_path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    json_path, ping_path = sidecar_paths(csv_path)
    meta = RunMeta()
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as fh:
            meta = RunMeta.from_dict(json.load(fh))
    ping: list[int] = []
    if os.path.exists(ping_path):
        with open(ping_path, encoding="utf-8-sig", errors="replace") as fh:
            ping = parse_ping_text(fh.read())
    return Run(csv_path, meta, parse_csv_text(text), text.splitlines(), ping)


def save_run(run: Run, ping_text: str | None = None) -> None:
    os.makedirs(os.path.dirname(run.path) or ".", exist_ok=True)
    lines = run.raw_lines if run.raw_lines else [HEADER] + [
        ",".join(str(int(t.trial)) if k == "trial" else ("nan" if math.isnan(t.get(k)) else f"{t.get(k):.2f}")
                 for k in HEADER.split(",")) for t in run.trials]
    with open(run.path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    json_path, ping_path = sidecar_paths(run.path)
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(run.meta.to_json())
    if ping_text:
        with open(ping_path, "w", encoding="utf-8") as fh:
            fh.write(ping_text)


def list_runs(folder: str) -> list[Run]:
    """All runs in a folder (non-recursive), oldest first."""
    runs = []
    if not os.path.isdir(folder):
        return runs
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".csv"):
            try:
                runs.append(load_run(os.path.join(folder, name)))
            except OSError:
                continue
    runs.sort(key=lambda r: (r.meta.started or "", r.path))
    return runs


# ---------------------------------------------------------------- statistics

def clean(x) -> np.ndarray:
    a = np.asarray(x, float)
    return a[~np.isnan(a)]


def summarize(x) -> dict | None:
    a = clean(x)
    n = len(a)
    if n == 0:
        return None
    return dict(n=n, mean=float(a.mean()), sd=float(a.std(ddof=1)) if n > 1 else 0.0, median=float(np.median(a)),
                min=float(a.min()), max=float(a.max()), p5=float(np.percentile(a, 5)), p95=float(np.percentile(a, 95)))


def welch(a, b) -> dict | None:
    """Difference of means a - b with a 95% confidence interval (Welch)."""
    a, b = clean(a), clean(b)
    if len(a) < 2 or len(b) < 2:
        return None
    t, p = sps.ttest_ind(a, b, equal_var=False)
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    if va + vb == 0:
        return dict(diff=float(a.mean() - b.mean()), lo=NAN, hi=NAN, t=NAN, p=NAN, df=NAN, na=len(a), nb=len(b))
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    d = a.mean() - b.mean()
    h = sps.t.ppf(0.975, df) * math.sqrt(va + vb)
    return dict(diff=float(d), lo=float(d - h), hi=float(d + h), t=float(t), p=float(p), df=float(df), na=len(a), nb=len(b))


def paired(a, b) -> dict | None:
    """Paired difference a - b over trials where both are present."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~np.isnan(a) & ~np.isnan(b)
    if m.sum() < 2:
        return None
    d = a[m] - b[m]
    t, p = sps.ttest_rel(a[m], b[m])
    return dict(diff=float(d.mean()), sd=float(d.std(ddof=1)), n=int(m.sum()), t=float(t), p=float(p))


def run_summary(run: Run) -> dict[str, dict | None]:
    return {k: summarize(run.values(k)) for k in MEASURES}


def fmt(x, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{digits}f}"


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()
