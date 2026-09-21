import math

import numpy as np
import pytest

from endolatency import analysis, model, placement


def synthetic_run(folder, label, rise_a, link, n=20, seed=0, ping=None):
    """Write a two-screen run with made-up latencies (normal around rise_a and rise_a + link)."""
    rng = np.random.default_rng(seed)
    trials = []
    for i in range(1, n + 1):
        a = float(rise_a + rng.normal(0, 10))
        b = float(a + link + rng.normal(0, 10))
        fa, fb = a + 10, b + 10
        trials.append(model.Trial(i, 0, 0, 50, 40, a, b, fa, fb, b - a, fb - fa))
    run = model.Run(str(folder / f"{label.replace(' ', '_')}.csv"), model.RunMeta(label=label, started=f"2000-01-01T10:{seed:02d}:00"),
                    trials)
    ping_text = "".join(f"Reply from mentor-host: bytes=32 time={t}ms TTL=64\n" for t in ping) if ping else None
    model.save_run(run, ping_text)
    return run


@pytest.fixture
def runs_dir(tmp_path):
    synthetic_run(tmp_path, "quiet link", 200, 20, seed=1, ping=[5] * 30)
    synthetic_run(tmp_path, "slow link", 200, 200, seed=2, ping=[5, 250] * 15)
    return tmp_path


def test_parse_both_formats():
    t = model.parse_trial_line("3,0,0,40,30,200.00,500.00,210.00,480.00,300.00,270.00")
    assert t.trial == 3 and t.riseB_ms == 500.0 and t.linkFall_ms == 270.0
    legacy = model.parse_trial_line("5,0,0,40,40,250.00,270.00,20.00")
    assert legacy.riseA_ms == 250.0 and math.isnan(legacy.fallA_ms) and abs(legacy.linkRise_ms - 20.0) < 1e-6
    missing = model.parse_trial_line("1,0,0,0,0,-1,-1,-1")
    assert math.isnan(missing.riseA_ms) and math.isnan(missing.linkRise_ms)
    assert model.parse_trial_line("# threshold=30") is None
    assert model.parse_trial_line("raw,12,40") is None


def test_parse_csv_uses_last_header():
    text = "14,0,0,50,90,-1,-1,-1\ntrial,baseA,baseB,peakA,peakB,mentee_ms,mentor_ms,link_ms\n1,0,0,0,0,-1,-1,-1\n# threshold=30\n2,0,0,40,40,250.00,270.00,20.00\n"
    trials = model.parse_csv_text(text)
    assert [t.trial for t in trials] == [1, 2]


def test_ping_parse():
    assert model.parse_ping_text("Reply from mentor-host: bytes=32 time=5ms TTL=64\ntime<1ms\n64 bytes: time=12.4 ms") == [5, 1, 12]


def test_runs_round_trip_and_summarise(runs_dir):
    runs = model.list_runs(str(runs_dir))
    assert len(runs) == 2
    by = {r.meta.label: r for r in runs}
    s = model.run_summary(by["quiet link"])
    assert s["riseA_ms"]["n"] == 20 and 190 < s["riseA_ms"]["mean"] < 210
    assert len(by["slow link"].ping) == 30
    w = model.welch(by["slow link"].values("linkRise_ms"), by["quiet link"].values("linkRise_ms"))
    assert w["diff"] > 150 and w["p"] < 1e-6


def test_verdicts_and_guide():
    assert placement.sensor_verdict(0, 45).status == "good"
    assert placement.sensor_verdict(8, 16).status == "weak"
    assert placement.sensor_verdict(27, 27).status == "none"
    assert placement.sensor_verdict(950, 1010).status == "saturated"
    assert placement.sensor_verdict(8, 16).threshold == 4
    assert "Sensor A" in placement.placement_guide("two-screen", "dark")
    assert "Find LED spot" in placement.placement_guide("one-screen-b", "lit")


def test_best_window_finds_bright_patch():
    off = np.full((200, 300, 3), 40, np.uint8)
    on = off.copy()
    on[100:160, 200:260] = 230
    cx, cy, gain, lo, ln = placement.best_window(off, on, 60)
    assert abs(cx - 230) <= 2 and abs(cy - 130) <= 2 and ln > lo


def test_report_export(runs_dir, tmp_path):
    runs = model.list_runs(str(runs_dir))
    html_path, _pdf = analysis.export_report(runs, str(tmp_path / "r.html"))
    text = open(html_path, encoding="utf-8").read()
    assert "Summary statistics" in text and "data:image/png;base64" in text
