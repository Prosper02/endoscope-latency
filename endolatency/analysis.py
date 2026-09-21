"""Figures, comparisons and report export for one or more runs.

All plotting functions draw into a matplotlib Figure passed in by the caller, so
they work both embedded in the Qt window and headless for report export.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import os
import shutil
import subprocess
import tempfile
import textwrap

import numpy as np
from matplotlib.figure import Figure

from .model import MEASURES, SCENES, SETUPS, Run, clean, fmt, measures_for_setup, paired, run_summary, summarize, welch

BLUE, ORANGE, AQUA, BLUE_LIGHT, ORANGE_LIGHT = "#2a78d6", "#eb6834", "#1baf7a", "#86b6ef", "#f5b39a"
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"


def _style(ax) -> None:
    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.label.set_color(INK2)
    ax.xaxis.label.set_color(INK2)
    ax.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)


def _title(ax, text: str) -> None:
    ax.set_title(text, loc="left", fontsize=9.5, fontweight="600", color=INK)


def _wrap(name: str, width: int = 18) -> str:
    return textwrap.fill(name, width)


# ---------------------------------------------------------------- figures

def fig_live(fig: Figure, trials, setup: str = "two-screen") -> None:
    """Per-trial rise (and fall, lighter) latencies during a run."""
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax)
    x = [t.trial for t in trials]
    if setup != "one-screen-b":
        ax.plot(x, [t.riseA_ms for t in trials], "o", ms=5, color=BLUE, mec="white", mew=0.8, label="Sensor A rise")
        ax.plot(x, [t.fallA_ms for t in trials], "o", ms=4, color=BLUE_LIGHT, mec="white", mew=0.8, label="Sensor A fall")
    if setup != "one-screen-a":
        ax.plot(x, [t.riseB_ms for t in trials], "o", ms=5, color=ORANGE, mec="white", mew=0.8, label="Sensor B rise")
        ax.plot(x, [t.fallB_ms for t in trials], "o", ms=4, color=ORANGE_LIGHT, mec="white", mew=0.8, label="Sensor B fall")
    ax.set_xlabel("Trial")
    ax.set_ylabel("LED event to screen (ms)")
    if trials:
        ax.legend(loc="upper left", ncol=2, frameon=False, fontsize=8)
        ax.set_ylim(bottom=0)
    else:
        ax.set_xlim(0, 20)
        ax.set_ylim(0, 500)
    _title(ax, "Live latencies")
    fig.tight_layout()


def fig_timeline(fig: Figure, runs: list[Run]) -> None:
    """Rise latency of every trial of the selected runs in order, with run boundaries."""
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax)
    i = 0
    xs, ya, yb, spans = [], [], [], []
    for r in runs:
        s = i + 1
        for t in r.trials:
            i += 1
            xs.append(i)
            ya.append(t.riseA_ms)
            yb.append(t.riseB_ms)
        spans.append((r.name, s, i))
    ax.plot(xs, ya, "o", ms=4.5, color=BLUE, mec="white", mew=0.8, label="Sensor A")
    ax.plot(xs, yb, "o", ms=4.5, color=ORANGE, mec="white", mew=0.8, label="Sensor B")
    top = np.nanmax(np.array(ya + yb, float)) if xs else 100
    for name, s, e in spans:
        if s > 1:
            ax.axvline(s - 0.5, color=AXIS, lw=0.8)
        ax.text((s + e) / 2, top * 1.02, _wrap(name, 16), ha="center", va="bottom", fontsize=6.5, color=INK2)
    ax.set_xlabel("Trial, chronological across the selected runs")
    ax.set_ylabel("LED on to screen bright (ms)")
    ax.set_ylim(0, top * 1.18)
    ax.legend(loc="lower left", ncol=2, frameon=False, fontsize=8)
    ax.grid(axis="x", visible=False)
    _title(ax, "Rise latency per trial")
    fig.tight_layout()


def fig_link(fig: Figure, runs: list[Run]) -> None:
    """Strip plot of B minus A (rise edges) per run with mean and SD."""
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax)
    rng = np.random.default_rng(3)
    labels = []
    for k, r in enumerate(runs):
        v = clean(r.values("linkRise_ms"))
        labels.append(r.name)
        if len(v) == 0:
            continue
        ax.plot(k + rng.uniform(-0.16, 0.16, len(v)), v, "o", ms=4.5, color=AQUA, mec="white", mew=0.8)
        mu = v.mean()
        sd = v.std(ddof=1) if len(v) > 1 else 0
        ax.errorbar(k + 0.3, mu, yerr=sd, fmt="o", ms=5, color=INK, ecolor=INK, capsize=3, lw=1.1)
        ax.text(k + 0.36, mu, f"{mu:.0f} ms\nSD {sd:.0f}\nn={len(v)}", va="center", fontsize=7, color=INK2)
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.set_xticks(range(len(runs)))
    ax.set_xticklabels([_wrap(l) for l in labels], fontsize=7)
    ax.set_xlim(-0.5, len(runs) - 0.5 + 0.6)
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("Sensor B minus sensor A (ms)")
    _title(ax, "Link delay per trial (rise edges)")
    fig.tight_layout()


def fig_bars(fig: Figure, runs: list[Run]) -> None:
    """Mean and SD of rise and fall latency for each sensor and run."""
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax)
    series = [("riseA_ms", "A rise", BLUE), ("fallA_ms", "A fall", BLUE_LIGHT), ("riseB_ms", "B rise", ORANGE), ("fallB_ms", "B fall", ORANGE_LIGHT)]
    y = np.arange(len(runs))
    h = 0.18
    xmax = 10
    for j, (key, lab, col) in enumerate(series):
        mus, sds = [], []
        for r in runs:
            v = clean(r.values(key))
            mus.append(v.mean() if len(v) else np.nan)
            sds.append(v.std(ddof=1) if len(v) > 1 else 0)
        off = (j - 1.5) * (h + 0.02)
        ax.barh(y + off, mus, height=h, color=col, label=lab, xerr=sds, error_kw=dict(ecolor=INK2, lw=0.8, capsize=2))
        for yy, m, s in zip(y + off, mus, sds):
            if not np.isnan(m):
                ax.text(m + s + 4, yy, f"{m:.0f}", va="center", fontsize=7, color=INK2)
                xmax = max(xmax, m + s)
    ax.set_yticks(y)
    ax.set_yticklabels([_wrap(r.name, 24) for r in runs], fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlim(0, xmax * 1.25)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("LED event to screen response, mean and SD (ms)")
    ax.legend(loc="lower right", frameon=False, fontsize=8)
    _title(ax, "Latency by run, sensor and edge")
    fig.tight_layout()


def fig_ping(fig: Figure, runs: list[Run]) -> None:
    """RTT traces of the runs that have a ping record."""
    fig.clear()
    withping = [r for r in runs if r.ping]
    if not withping:
        ax = fig.add_subplot(111)
        _style(ax)
        ax.text(0.5, 0.5, "No ping record in the selected runs", ha="center", va="center", color=INK2, transform=ax.transAxes)
        fig.tight_layout()
        return
    axs = fig.subplots(len(withping), 1, sharey=True)
    if len(withping) == 1:
        axs = [axs]
    ymax = max(max(r.ping) for r in withping)
    for ax, r in zip(axs, withping):
        _style(ax)
        s = r.ping
        x = np.arange(1, len(s) + 1)
        ax.plot(x, s, "-", lw=1.3, color=AQUA)
        ax.plot(x, s, "o", ms=2.4, color=AQUA)
        p95 = np.percentile(s, 95)
        ax.axhline(p95, color=AXIS, lw=0.8)
        ax.text(0.995, 0.9, f"median {np.median(s):.0f} ms, p95 {p95:.0f} ms, max {max(s)} ms", transform=ax.transAxes, ha="right", va="top", fontsize=7.5, color=INK2)
        _title(ax, f"{r.name}: {len(s)} replies to {r.meta.ping_host or 'host'}")
        ax.set_ylabel("RTT (ms)")
        ax.grid(axis="x", visible=False)
        ax.set_ylim(0, max(50, ymax * 1.15))
    axs[-1].set_xlabel("Ping number")
    fig.tight_layout()


FIGURES = {
    "Rise latency per trial": fig_timeline,
    "Link delay (B minus A)": fig_link,
    "Mean latency by run": fig_bars,
    "Ping RTT": fig_ping,
}


# ---------------------------------------------------------------- comparisons

def compare_runs(a: Run, b: Run) -> list[dict]:
    """Welch comparison a - b for every measure present in both runs."""
    out = []
    for key, label in MEASURES.items():
        w = welch(a.values(key), b.values(key))
        if w:
            out.append(dict(key=key, label=label, **w))
    return out


def edge_symmetry(run: Run) -> dict[str, dict | None]:
    return {"A": paired(run.values("fallA_ms"), run.values("riseA_ms")), "B": paired(run.values("fallB_ms"), run.values("riseB_ms"))}


def ping_summary(rtts: list[int]) -> dict | None:
    if not rtts:
        return None
    s = np.array(rtts, float)
    return dict(n=len(s), median=float(np.median(s)), mean=float(s.mean()), p95=float(np.percentile(s, 95)), max=int(s.max()), spikes=int((s > 100).sum()))


# ---------------------------------------------------------------- report export

def find_browser() -> str | None:
    """A Chromium-based browser for headless PDF printing, or None."""
    cands = []
    for env in ("ENDOLATENCY_BROWSER", "CHROME_PATH"):
        if os.environ.get(env):
            cands.append(os.environ[env])
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("LOCALAPPDATA", "")):
            cands += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"), os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"):
            w = shutil.which(name)
            if w:
                cands.append(w)
        cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def html_to_pdf(html_path: str, pdf_path: str, timeout: int = 120) -> bool:
    browser = find_browser()
    if not browser:
        return False
    profile = tempfile.mkdtemp(prefix="endolatency-pdf-")
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", f"--user-data-dir={profile}", "--no-pdf-header-footer",
           "--virtual-time-budget=10000", f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0


def _fig_png(draw, runs, size=(7.2, 3.2)) -> str:
    fig = Figure(figsize=size, dpi=170)
    draw(fig, runs)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _tbl(head, rows, cls="") -> str:
    h = "".join(f"<th>{html.escape(str(c))}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


_CSS = """
@page { size: A4; margin: 17mm 16mm; }
body { font-family: "Segoe UI", system-ui, -apple-system, sans-serif; font-size: 10pt; color: #0b0b0b; line-height: 1.42; margin: 0 auto; max-width: 190mm; padding: 0 8px; }
h1 { font-size: 20pt; margin: 0 0 4pt; } h2 { font-size: 13pt; margin: 18pt 0 6pt; border-bottom: 1px solid #c3c2b7; padding-bottom: 3pt; } h3 { font-size: 11pt; margin: 12pt 0 4pt; }
.meta { color: #52514e; font-size: 9.5pt; margin-bottom: 12pt; }
table { border-collapse: collapse; font-size: 8.8pt; font-variant-numeric: tabular-nums; width: 100%; margin: 4pt 0 9pt; }
th, td { padding: 2.5pt 6pt; border-bottom: 1px solid #e1e0d9; text-align: right; vertical-align: top; }
th:first-child, td:first-child { text-align: left; } th { color: #52514e; font-weight: 600; border-bottom: 1px solid #c3c2b7; }
table.left td, table.left th { text-align: left; } table.small { font-size: 7.6pt; }
thead { display: table-header-group; } tr { page-break-inside: avoid; }
figure { margin: 8pt 0 10pt; page-break-inside: avoid; } figure img { width: 100%; display: block; } figcaption { font-size: 8.8pt; color: #52514e; margin-top: 4pt; }
.pb { page-break-before: always; } .foot { color: #898781; font-size: 8.5pt; margin-top: 14pt; }
"""

SH = ["Run", "Measure", "n", "Mean", "SD", "Median", "Min", "Max", "p5", "p95"]


def summary_rows(runs: list[Run]) -> list[list[str]]:
    rows = []
    for r in runs:
        s = run_summary(r)
        for key in measures_for_setup(r.meta.setup):
            v = s.get(key)
            if v:
                rows.append([html.escape(r.name), MEASURES[key], str(v["n"]), fmt(v["mean"]), fmt(v["sd"]), fmt(v["median"]), fmt(v["min"]), fmt(v["max"]), fmt(v["p5"]), fmt(v["p95"])])
    return rows


def export_report(runs: list[Run], out_html: str, title: str = "Endoscope latency measurements") -> tuple[str, str | None]:
    """Write an HTML report for the runs; also a PDF next to it when a Chromium browser is available."""
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{_CSS}</style></head><body>",
             f"<h1>{html.escape(title)}</h1>",
             f"<div class='meta'>Generated {dt.datetime.now():%Y-%m-%d %H:%M} by EndoLatency from {len(runs)} run(s).</div>"]

    parts.append("<h2>1. Runs and conditions</h2>")
    rows = []
    for r in runs:
        m = r.meta
        rows.append([html.escape(r.name), html.escape(m.started[:16].replace("T", " ")), html.escape(SETUPS.get(m.setup, m.setup)), html.escape(m.application),
                     html.escape(SCENES.get(m.scene, m.scene)), html.escape(m.sensorA), html.escape(m.sensorB), str(m.threshold), str(len(r.trials)), html.escape(m.notes)])
    parts.append(_tbl(["Run", "Started", "Setup", "Application", "Scene", "Sensor A", "Sensor B", "Threshold", "Trials", "Notes"], rows, "left small"))

    parts.append("<h2>2. Summary statistics</h2><p>Latencies in milliseconds from the LED switching to the screen crossing its detection level. "
                 "Sensor B minus sensor A is the delay added between the two screens (the telementoring link in a two-screen setup).</p>")
    parts.append(_tbl(SH, summary_rows(runs)))

    parts.append("<h2>3. Figures</h2>")
    parts.append(f"<figure><img src='{_fig_png(fig_timeline, runs)}'><figcaption><b>Figure 1.</b> Rise latency of every trial in chronological order.</figcaption></figure>")
    parts.append(f"<figure><img src='{_fig_png(fig_bars, runs, (7.2, 0.9 + 0.75 * len(runs)))}'><figcaption><b>Figure 2.</b> Mean and SD of rise and fall latency per sensor and run.</figcaption></figure>")
    if any(len(clean(r.values('linkRise_ms'))) for r in runs):
        parts.append(f"<figure><img src='{_fig_png(fig_link, runs)}'><figcaption><b>Figure 3.</b> Sensor B minus sensor A per trial, rise edges.</figcaption></figure>")
    if any(r.ping for r in runs):
        n = sum(1 for r in runs if r.ping)
        parts.append(f"<figure><img src='{_fig_png(fig_ping, runs, (7.2, 1.0 + 1.5 * n))}'><figcaption><b>Figure 4.</b> Round-trip time to the mentor host during the runs that recorded one.</figcaption></figure>")

    parts.append("<h2>4. Comparisons</h2>")
    if len(runs) >= 2:
        crow = []
        base = runs[0]
        for r in runs[1:]:
            for c in compare_runs(r, base):
                crow.append([html.escape(f"{r.name} minus {base.name}"), c["label"], f"{c['diff']:+.1f}", f"{c['lo']:+.1f} to {c['hi']:+.1f}", f"{c['p']:.2g}", f"{c['na']} vs {c['nb']}"])
        parts.append(f"<p>Each run compared with the first selected run ({html.escape(base.name)}), Welch's t-test.</p>")
        parts.append(_tbl(["Comparison", "Measure", "Difference (ms)", "95% CI", "p", "n"], crow))
    else:
        parts.append("<p>Select two or more runs to compare them.</p>")
    erow = []
    for r in runs:
        for ch, e in edge_symmetry(r).items():
            if e:
                erow.append([html.escape(r.name), f"Sensor {ch}: fall minus rise", f"{e['diff']:+.1f}", fmt(e["sd"]), str(e["n"]), f"{e['p']:.2g}"])
    if erow:
        parts.append("<h3>Edge symmetry (paired within trials)</h3>")
        parts.append(_tbl(["Run", "Measure", "Difference (ms)", "SD", "n", "p"], erow))
    prow = []
    for r in runs:
        ps = ping_summary(r.ping)
        if ps:
            prow.append([html.escape(r.name), html.escape(r.meta.ping_host), str(ps["n"]), fmt(ps["median"], 0), fmt(ps["mean"]), fmt(ps["p95"], 0), str(ps["max"]), str(ps["spikes"])])
    if prow:
        parts.append("<h3>Network round-trip time</h3>")
        parts.append(_tbl(["Run", "Host", "Replies", "Median ms", "Mean ms", "p95 ms", "Max ms", "Replies > 100 ms"], prow))

    parts.append("<h2 class='pb'>Appendix. Per-trial data</h2><p>Dark and lit levels are 10-bit ADC counts; times in milliseconds; \u2014 marks an edge not detected or not attempted.</p>")
    for r in runs:
        rows = [[str(t.trial), fmt(t.baseA, 0), fmt(t.baseB, 0), fmt(t.litA, 0), fmt(t.litB, 0), fmt(t.riseA_ms), fmt(t.riseB_ms), fmt(t.fallA_ms), fmt(t.fallB_ms), fmt(t.linkRise_ms), fmt(t.linkFall_ms)] for t in r.trials]
        parts.append(f"<h3>{html.escape(r.name)}</h3>" + _tbl(["Trial", "Dark A", "Dark B", "Lit A", "Lit B", "Rise A", "Rise B", "Fall A", "Fall B", "B-A rise", "B-A fall"], rows, "small"))
    parts.append("<p class='foot'>EndoLatency report. Regenerate from the run files with the Analysis tab.</p></body></html>")

    os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    pdf = os.path.splitext(out_html)[0] + ".pdf"
    return out_html, (pdf if html_to_pdf(out_html, pdf) else None)
