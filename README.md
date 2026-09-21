# EndoLatency

Desktop tool for measuring the glass-to-glass latency of an endoscope video chain and of a
telementoring link, using an Arduino UNO, two TEMT6000 light sensors and one LED.
It drives the measurement, guides sensor placement, analyses the results and exports a report,
for either a **two-screen** setup (mentee screen plus mentor screen) or a **one-screen** setup.

The method, the numbers it produced on 21 September 2026 and their interpretation are in
[docs/2026-09-21_endoscope_latency_report.pdf](docs/2026-09-21_endoscope_latency_report.pdf).
Those ten runs ship in `sample_data/` and open from the Analysis tab.

## How it works

The Arduino switches a white LED that sits in the endoscope's field of view and time-stamps
the switch. A light sensor taped to each screen tells the Arduino when the flash appears
(rise edge) and disappears (fall edge). Both screens are timed by the same microcontroller
clock, so the difference between them, the link delay, needs no clock synchronisation between
computers. The firmware reports one CSV line per trial; the app records, saves and analyses them.

```
Arduino UNO ──A2──▶ LED ──▶ endoscope camera ──▶ capture ──▶ mentee PC ──▶ mentee screen ◀── sensor A (A0)
                                                                 │
                                                          telementoring link
                                                                 ▼
                                                         mentor PC ──▶ mentor screen ◀── sensor B (A1)
```

## Hardware

| Part | Connection |
|---|---|
| Arduino UNO | USB to the measurement PC |
| TEMT6000 breakout, sensor A | VCC → 5V, GND → GND, SIG → A0. Tape to the mentee (or only) screen |
| TEMT6000 breakout, sensor B | VCC → 5V, GND → GND, SIG → A1. Tape to the mentor screen (two-screen setup) |
| White LED | A2 → LED anode, cathode → 220 Ω → GND. Fix it inside the endoscope's field of view |

Sensor leads of several metres are fine (three wires). Shield each sensor from room light with
black tape or a cap and run the screens at maximum brightness.

## Firmware

Flash `firmware/endoscope_latency/endoscope_latency.ino` once, with the Arduino IDE or:

```
arduino-cli compile --fqbn arduino:avr:uno firmware/endoscope_latency
arduino-cli upload  --fqbn arduino:avr:uno -p COM5 firmware/endoscope_latency
```

Serial protocol at 115200 baud: `r` raw readings with LED off, `l` raw readings with LED on,
`m` measure, `t<n>` detection threshold, `a`/`b`/`x` time sensor A, sensor B or both.
Each trial prints `trial,baseA,baseB,litA,litB,riseA_ms,riseB_ms,fallA_ms,fallB_ms,linkRise_ms,linkFall_ms`.

## Install

**Windows, nothing else needed.** Download `EndoLatency-win32.zip` from the
[Releases](../../releases) page, extract it anywhere, and run `EndoLatency.exe` inside the
folder. The zip is built automatically by GitHub Actions whenever a version tag is pushed
(`git tag v0.1.0 && git push --tags`). To build it yourself: `pip install pyinstaller` then
`python build_exe.py`; the result is `dist/EndoLatency/` and `dist/EndoLatency-win32.zip`.

**With Python (Windows, macOS, Linux).** Python 3.10 or newer:

```
pip install git+https://github.com/Prosper02/endoscope-latency.git
endolatency
```

The sample data and firmware are not part of a pip install; clone the repository to get them.

**From source.**

```
git clone https://github.com/Prosper02/endoscope-latency.git
cd endoscope-latency
pip install -r requirements.txt
python -m endolatency
```

On Windows, `run_endolatency.bat` does the same from a double-click and installs the
dependencies the first time. Send a shortcut to it (or to `EndoLatency.exe`) to the Desktop
for daily use.

PDF export uses Chrome or Edge if installed; otherwise the HTML report is written and can be
printed from any browser. The Arduino needs the firmware flashed once (see above); the app
itself needs no driver beyond the one Windows installs for the UNO.

## Using the app

1. **Connect.** Pick the Arduino's port and press Connect. The board resets and reports its
   firmware header; the app then parks it with the LED off.
2. **Choose the setup** in the top bar: two screens (sensor A mentee, sensor B mentor), or one
   screen with sensor A or sensor B.
3. **Placement tab.** Read the guide for the chosen setup and scene. Use *Live, LED off* and
   *Live, LED on* while sliding a sensor to find the flash, *Check swing* to get a verdict per
   sensor and a recommended threshold, and *Find LED spot* to have the app screenshot every
   display with the LED off and on, mark where the flash changes the picture most, and put a
   red ring on the screen at that position. Lit-scene measurements depend on this.
4. **Measure tab.** Fill in the conditions (application, scene, sensor positions, notes), set
   threshold and duration, optionally a mentor host to ping during the run, and press *Start run*.
   Trials appear live with a plot and running means. *Stop and save* (or the timer) writes three
   files per run into the session folder: `<stem>.csv` (serial log), `<stem>.json` (conditions)
   and `<stem>.ping.txt` (RTT record).
5. **Analysis tab.** Select runs to see summary statistics, four figures, Welch comparisons
   against the first selected run, rise/fall edge symmetry and RTT summaries. *Export report*
   writes an HTML report with embedded figures and per-trial appendix, plus a PDF when a
   Chromium browser is available.

## Variations the app supports

| Variation | What to set |
|---|---|
| Two screens, telementoring link | Setup: two screens; ping host = mentor machine; sensor A on the mentee screen, sensor B on the mentor screen |
| One screen, local chain only | Setup: one screen (A or B); application = the viewer under test |
| Dark scene (light source off) | Scene: dark. The LED is the only light; threshold 30 usually works |
| Light source on | Scene: lit. Use *Find LED spot*, place the sensor there, then *Check swing* and the recommended threshold |
| Different viewer applications | Change *Application on screen* between runs and compare them in Analysis |
| Network conditions | Enter the mentor host; the RTT record is saved with the run and summarised next to the link delay |

## Repository layout

```
endolatency/        the application (model, serial link, placement, analysis, Qt window, icon)
firmware/           Arduino sketch
sample_data/        the ten runs of 21 September 2026 with metadata and ping records
docs/               technical report of those runs
tests/              pytest suite (python -m pytest)
build_exe.py        PyInstaller build of the self-contained Windows folder
.github/workflows/  CI: tests on every push, Windows package on every v* tag
```

## Results file format

`.csv` is the firmware's serial output as received, comment lines included, so it can be
re-parsed later. `.json` holds `label, setup, application, scene, sensorA, sensorB, threshold,
channels, notes, started, ended, ping_host, firmware`. `.ping.txt` is the raw output of the
system `ping`.

## Known limits

- Single trials are quantised by the display refresh (about ±8 ms per screen at 60 Hz) and the
  camera frame period; report means over 15 or more trials.
- A ping run beside the trials shows network state but is not proof of causation for link delay
  changes; WebRTC statistics from the application would close that gap.
- The on-screen marker maps screenshot pixels to Qt screen coordinates by matching monitor sizes;
  on unusual multi-monitor DPI layouts check the marker against the screenshot shown in the app.

## License

MIT.
