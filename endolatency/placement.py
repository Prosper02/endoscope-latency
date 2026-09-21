"""Sensor placement helpers: reading quality verdicts and finding where the LED shows on screen.

Everything here is Qt-free so it can be tested without a display.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

try:
    import mss
except ImportError:  # pragma: no cover
    mss = None


# ---------------------------------------------------------------- reading quality

@dataclass
class Verdict:
    status: str          # good | ok | weak | none | saturated | leak
    message: str
    threshold: int       # recommended detection threshold (ADC counts above dark)
    dark: float
    lit: float

    @property
    def swing(self) -> float:
        return self.lit - self.dark


def sensor_verdict(dark: float, lit: float) -> Verdict:
    """Judge a sensor position from its LED-off (dark) and LED-on (lit) readings, 10-bit ADC counts."""
    swing = lit - dark
    thr = int(max(4, min(30, round(swing / 2)))) if swing >= 4 else 4
    if lit >= 1000 or dark >= 900:
        return Verdict("saturated", "Sensor saturated. Lower the screen brightness or move the sensor to a darker part of the image.", thr, dark, lit)
    if dark >= 150 and swing < 30:
        return Verdict("leak", f"Dark level {dark:.0f} is high: room light is reaching the sensor. Shield it with tape or a cap, then re-check.", thr, dark, lit)
    if swing < 4:
        return Verdict("none", f"No usable change ({dark:.0f} to {lit:.0f}). The LED is not visible where the sensor sits. Use 'Find LED spot' or move the sensor onto the flash.", thr, dark, lit)
    if swing < 12:
        return Verdict("weak", f"Weak signal: swing {swing:.0f} counts. Measurable at threshold {thr} but with little margin; moving the sensor onto the brightest part of the flash is better.", thr, dark, lit)
    if swing < 30:
        return Verdict("ok", f"Usable: swing {swing:.0f} counts. Set the threshold to about {thr}.", thr, dark, lit)
    return Verdict("good", f"Good: swing {swing:.0f} counts. The default threshold of 30 works; {thr} gives the same result with more margin.", thr, dark, lit)


# ---------------------------------------------------------------- screenshots

@dataclass
class Monitor:
    index: int           # 1-based, in mss order
    left: int
    top: int
    width: int
    height: int


@dataclass
class Shot:
    monitor: Monitor
    image: np.ndarray    # H x W x 3 uint8 RGB


def grab_monitors() -> list[Shot]:
    """Screenshot of every monitor in physical pixels."""
    if mss is None:
        raise RuntimeError("The 'mss' package is required for screenshots (pip install mss).")
    shots = []
    with mss.mss() as sct:
        for i, m in enumerate(sct.monitors[1:], start=1):
            raw = sct.grab(m)
            img = np.asarray(Image.frombytes("RGB", raw.size, raw.rgb))
            shots.append(Shot(Monitor(i, m["left"], m["top"], m["width"], m["height"]), img))
    return shots


# ---------------------------------------------------------------- LED spot search

@dataclass
class Spot:
    monitor: Monitor
    cx: int              # centre of the best window, pixels within the monitor
    cy: int
    window: int
    gain: float          # mean change in gamma-corrected light output inside the window (0..1 scale)
    lum_off: float       # mean 8-bit luminance inside the window, LED off
    lum_on: float
    pct_x: float         # centre as a fraction of the monitor width/height
    pct_y: float

    @property
    def ratio(self) -> float:
        lo = max((self.lum_off / 255.0) ** 2.2, 1e-4)
        return (self.lum_on / 255.0) ** 2.2 / lo

    @property
    def abs_x(self) -> int:
        return self.monitor.left + self.cx

    @property
    def abs_y(self) -> int:
        return self.monitor.top + self.cy


def _box_mean(a: np.ndarray, w: int) -> np.ndarray:
    c = np.cumsum(np.cumsum(np.pad(a, ((1, 0), (1, 0))), axis=0), axis=1)
    return (c[w:, w:] - c[:-w, w:] - c[w:, :-w] + c[:-w, :-w]) / float(w * w)


def best_window(off: np.ndarray, on: np.ndarray, window: int = 60) -> tuple[int, int, float, float, float]:
    """Centre (x, y), gain, and mean luminances of the window where LED-on brightens the image most."""
    lo = off.astype(np.float32).mean(axis=2)
    ln = on.astype(np.float32).mean(axis=2)
    if lo.shape[0] < window or lo.shape[1] < window:
        window = max(8, min(lo.shape) // 2)
    gain = _box_mean((ln / 255.0) ** 2.2, window) - _box_mean((lo / 255.0) ** 2.2, window)
    iy, ix = np.unravel_index(int(np.argmax(gain)), gain.shape)
    return ix + window // 2, iy + window // 2, float(gain[iy, ix]), float(_box_mean(lo, window)[iy, ix]), float(_box_mean(ln, window)[iy, ix])


def find_led_spots(off_shots: list[Shot], on_shots: list[Shot], window: int = 60) -> list[Spot]:
    """Best window on every monitor, strongest first. Pair shots by monitor index."""
    on_by_idx = {s.monitor.index: s for s in on_shots}
    spots = []
    for off in off_shots:
        on = on_by_idx.get(off.monitor.index)
        if on is None or on.image.shape != off.image.shape:
            continue
        cx, cy, gain, lo, ln = best_window(off.image, on.image, window)
        m = off.monitor
        spots.append(Spot(m, int(cx), int(cy), window, gain, lo, ln, cx / m.width, cy / m.height))
    spots.sort(key=lambda s: s.gain, reverse=True)
    return spots


def annotate(shot: Shot, spot: Spot, max_width: int = 1400) -> Image.Image:
    """The LED-on screenshot with the best window outlined in red, downscaled for display."""
    img = Image.fromarray(shot.image).convert("RGB")
    d = ImageDraw.Draw(img)
    h = spot.window // 2
    for k in range(6):
        d.rectangle([spot.cx - h - k, spot.cy - h - k, spot.cx + h + k, spot.cy + h + k], outline=(255, 0, 0))
    d.ellipse([spot.cx - 8, spot.cy - 8, spot.cx + 8, spot.cy + 8], fill=(255, 0, 0))
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    return img


def describe_spot(spot: Spot) -> str:
    return (f"Monitor {spot.monitor.index} ({spot.monitor.width}x{spot.monitor.height}): "
            f"{spot.pct_x * 100:.0f}% from the left edge, {spot.pct_y * 100:.0f}% from the top; "
            f"luminance {spot.lum_off:.0f} to {spot.lum_on:.0f} (8-bit), light output x{spot.ratio:.1f}")


# ---------------------------------------------------------------- guidance text

def placement_guide(setup: str, scene: str) -> str:
    """Step-by-step placement advice for the chosen variation."""
    common = [
        "Tape each TEMT6000 flat against the glass, sensing element towards the screen, and cover its back and edges with black tape or a cap.",
        "Set the screen to maximum brightness and turn off adaptive brightness.",
        "Keep the endoscope and the scene completely still during a run; any movement changes brightness under the sensor and can register as a false edge.",
    ]
    if scene == "dark":
        scene_steps = [
            "With the light source off the LED is the only light the camera sees, so the flash appears as a bright blob on an otherwise black image.",
            "Use 'Live readings' with the LED off: both sensors should read close to 0. If not, shield them better.",
            "Use 'Live readings' with the LED on and slide each sensor until its reading peaks; the centre of the blob gives a swing of 40 counts or more.",
            "The default threshold of 30 is right when the swing is above about 40 counts; 'Check swing' recommends a value otherwise.",
        ]
    else:
        scene_steps = [
            "With the light source on the flash is a small change on a bright image, so sensor position is critical.",
            "Use 'Find LED spot' to screenshot the display with the LED off and on; it marks the window where the LED changes the picture most and can show a marker on the screen.",
            "Place the sensor on that spot, then 'Check swing'. A swing of 12 counts or more is comfortable; below 4 counts nothing can be timed.",
            "Lower the threshold to roughly half the swing (the app suggests a value) and keep the scene static.",
        ]
    if setup == "two-screen":
        setup_steps = [
            "Sensor A (A0) goes on the mentee's local screen over the video window; sensor B (A1) goes on the mentor's screen over the received video.",
            "Both sensors are timed by the same Arduino, so the mentor screen must be within cable reach (three wires: 5 V, GND, SIG; several metres is fine).",
            "Optionally enter the mentor host's address so a ping batch runs alongside the trials; report the RTT distribution with the link delay.",
        ]
    elif setup == "one-screen-a":
        setup_steps = ["Only sensor A (A0) is timed. Place it on the screen showing the endoscope video; sensor B can stay connected but is ignored."]
    else:
        setup_steps = ["Only sensor B (A1) is timed. Place it on the screen showing the endoscope video; sensor A can stay connected but is ignored."]
    lines = ["Setup", *[f"  • {s}" for s in setup_steps], "", "Scene", *[f"  • {s}" for s in scene_steps], "", "Always", *[f"  • {s}" for s in common]]
    return "\n".join(lines)
