"""EndoLatency desktop window: Measure, Placement and Analysis tabs."""
from __future__ import annotations

import collections
import datetime as dt
import os
import re
import sys

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as Canvas
from matplotlib.figure import Figure
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from . import __version__
from .analysis import FIGURES, SH, compare_runs, edge_symmetry, export_report, fig_live, ping_summary, summary_rows
from .model import (APPLICATIONS, MEASURES, SCENES, SETUPS, Run, RunMeta, channels_for_setup, fmt, list_runs, measures_for_setup,
                    now_iso, run_summary, save_run)
from .placement import Spot, annotate, describe_spot, find_led_spots, grab_monitors, placement_guide, sensor_verdict
from .serial_link import SerialLink, PingWorker, available_ports

def resource_root() -> str:
    """Folder that holds sample_data/, firmware/ and endolatency/assets/ (repo root, site-packages, or the PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for base in (getattr(sys, "_MEIPASS", None), exe_dir, os.path.join(exe_dir, "_internal")):
            if base and os.path.isdir(os.path.join(base, "endolatency", "assets")):
                return base
        return exe_dir
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = resource_root()
ICON_PATH = os.path.join(ROOT, "endolatency", "assets", "icon.ico")
SAMPLE_DIR = os.path.join(ROOT, "sample_data", "2026-09-21")
DEFAULT_SESSIONS = os.path.join(os.path.expanduser("~"), "Documents", "EndoLatency", "sessions")


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "run"


def pil_to_pixmap(img) -> QPixmap:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, 3 * img.width, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


# ---------------------------------------------------------------- on-screen marker

class MarkerOverlay(QWidget):
    """A red ring drawn on top of everything at a screen position, closing itself after a while."""

    def __init__(self, screen, centre: QPoint, size: int = 140, seconds: int = 60):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setScreen(screen)
        self.setGeometry(centre.x() - size // 2, centre.y() - size // 2, size, size)
        QTimer.singleShot(seconds * 1000, self.close)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(255, 0, 0), 6)
        p.setPen(pen)
        s = min(self.width(), self.height())
        p.drawEllipse(6, 6, s - 12, s - 12)
        p.drawLine(s // 2, 24, s // 2, s - 24)
        p.drawLine(24, s // 2, s - 24, s // 2)


def physical_to_screen_point(spot: Spot):
    """Map a physical pixel on an mss monitor to (QScreen, logical QPoint)."""
    mon = spot.monitor
    best, best_d = None, None
    for s in QGuiApplication.screens():
        dpr = s.devicePixelRatio() or 1.0
        geo = s.geometry()
        if round(geo.width() * dpr) != mon.width or round(geo.height() * dpr) != mon.height:
            continue
        d = abs(geo.x() * dpr - mon.left) + abs(geo.y() * dpr - mon.top)
        if best is None or d < best_d:
            best, best_d = s, d
    if best is None:
        best = QGuiApplication.primaryScreen()
    dpr = best.devicePixelRatio() or 1.0
    geo = best.geometry()
    return best, QPoint(int(geo.x() + spot.cx / dpr), int(geo.y() + spot.cy / dpr))


# ---------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"EndoLatency {__version__}")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1280, 820)
        self.settings = QSettings("EndoLatency", "EndoLatency")
        self.link: SerialLink | None = None
        self.ping: PingWorker | None = None
        self.fw_ready = False
        self.measuring = False
        self.trials = []
        self.raw_lines: list[str] = []
        self.meta: RunMeta | None = None
        self.raw_history = collections.deque(maxlen=300)
        self.raw_collect: list[tuple[int, int]] | None = None
        self.spots: list[Spot] = []
        self.on_shots = []
        self.runs: list[Run] = []
        self.pending_ping_text: str | None = None
        self._build_ui()
        self._restore_settings()
        self.refresh_ports()
        self.load_runs()
        self.clock = QTimer(self)
        self.clock.setInterval(500)
        self.clock.timeout.connect(self._tick)

    # ------------------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        top = QHBoxLayout()
        top.addWidget(QLabel("Microcontroller port"))
        self.port_box = QComboBox()
        self.port_box.setMinimumWidth(220)
        top.addWidget(self.port_box)
        b = QPushButton("Refresh")
        b.clicked.connect(self.refresh_ports)
        top.addWidget(b)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connect)
        top.addWidget(self.connect_btn)
        self.fw_label = QLabel("Not connected")
        self.fw_label.setStyleSheet("color:#52514e")
        top.addWidget(self.fw_label, 1)
        top.addWidget(QLabel("Setup"))
        self.setup_box = QComboBox()
        for k, v in SETUPS.items():
            self.setup_box.addItem(v, k)
        self.setup_box.currentIndexChanged.connect(self._setup_changed)
        top.addWidget(self.setup_box)
        lay.addLayout(top)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_measure_tab(), "Measure")
        self.tabs.addTab(self._build_placement_tab(), "Placement")
        self.tabs.addTab(self._build_analysis_tab(), "Analysis")
        self.statusBar().showMessage("Connect the microcontroller, choose the setup, then place the sensors or start a run.")

    def _build_measure_tab(self) -> QWidget:
        w = QWidget()
        split = QSplitter(Qt.Horizontal, w)
        lay = QHBoxLayout(w)
        lay.addWidget(split)

        left = QWidget()
        form = QFormLayout(left)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("e.g. web app, dark scene, wired network")
        form.addRow("Run label", self.label_edit)
        self.app_box = QComboBox()
        self.app_box.setEditable(True)
        self.app_box.addItems(APPLICATIONS)
        form.addRow("Application on screen", self.app_box)
        self.scene_box = QComboBox()
        for k, v in SCENES.items():
            self.scene_box.addItem(v, k)
        self.scene_box.currentIndexChanged.connect(self._update_guide)
        form.addRow("Scene", self.scene_box)
        self.sensorA_edit = QLineEdit()
        self.sensorA_edit.setPlaceholderText("where sensor A (A0) is taped")
        form.addRow("Sensor A placement", self.sensorA_edit)
        self.sensorB_edit = QLineEdit()
        self.sensorB_edit.setPlaceholderText("where sensor B (A1) is taped")
        form.addRow("Sensor B placement", self.sensorB_edit)
        self.thr_spin = QSpinBox()
        self.thr_spin.setRange(2, 400)
        self.thr_spin.setValue(30)
        self.thr_spin.setToolTip("ADC counts above the dark level that count as 'screen bright'. 30 suits a dark scene; use Placement > Check swing for a recommendation.")
        form.addRow("Detection threshold", self.thr_spin)
        self.dur_spin = QSpinBox()
        self.dur_spin.setRange(0, 3600)
        self.dur_spin.setValue(120)
        self.dur_spin.setSuffix(" s")
        self.dur_spin.setToolTip("Run length. Each trial takes about 6 s. 0 = run until Stop.")
        form.addRow("Duration", self.dur_spin)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("mentor host IP or name (optional)")
        form.addRow("Ping during run", self.host_edit)
        self.notes_edit = QLineEdit()
        form.addRow("Notes", self.notes_edit)
        self.folder_edit = QLineEdit(DEFAULT_SESSIONS)
        fb = QPushButton("Browse")
        fb.clicked.connect(self.choose_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_edit, 1)
        row.addWidget(fb)
        form.addRow("Save runs to", row)
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start run")
        self.start_btn.setStyleSheet("font-weight:600; padding:6px 14px")
        self.start_btn.clicked.connect(self.start_run)
        self.stop_btn = QPushButton("Stop and save")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_run)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        form.addRow(btns)
        self.run_status = QLabel("Idle")
        self.run_status.setWordWrap(True)
        form.addRow(self.run_status)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.RichText)
        form.addRow(self.summary_label)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.live_canvas = Canvas(Figure(figsize=(6, 3.2)))
        self.live_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        rl.addWidget(self.live_canvas, 3)
        self.trial_table = QTableWidget(0, 11)
        self.trial_table.setHorizontalHeaderLabels(["Trial", "Dark A", "Dark B", "Lit A", "Lit B", "Rise A", "Rise B", "Fall A", "Fall B", "B-A rise", "B-A fall"])
        self.trial_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.trial_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rl.addWidget(self.trial_table, 2)
        split.addWidget(right)
        split.setSizes([420, 860])
        fig_live(self.live_canvas.figure, [], "two-screen")
        return w

    def _build_placement_tab(self) -> QWidget:
        w = QWidget()
        split = QSplitter(Qt.Horizontal, w)
        lay = QHBoxLayout(w)
        lay.addWidget(split)

        left = QWidget()
        ll = QVBoxLayout(left)
        g = QGroupBox("Live sensor readings")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        b1 = QPushButton("Live, LED off")
        b1.clicked.connect(lambda: self.send("r"))
        b2 = QPushButton("Live, LED on")
        b2.clicked.connect(lambda: self.send("l"))
        b3 = QPushButton("Check swing")
        b3.setToolTip("Reads both sensors with the LED off then on and recommends a threshold")
        b3.clicked.connect(self.check_swing)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addWidget(b3)
        gl.addLayout(row)
        self.raw_label = QLabel("A0: —    A1: —")
        self.raw_label.setStyleSheet("font-size:20px; font-weight:600")
        gl.addWidget(self.raw_label)
        self.verdict_label = QLabel("Use 'Check swing' after placing the sensors.")
        self.verdict_label.setWordWrap(True)
        gl.addWidget(self.verdict_label)
        self.apply_thr_btn = QPushButton("Use recommended threshold in Measure tab")
        self.apply_thr_btn.setEnabled(False)
        self.apply_thr_btn.clicked.connect(self.apply_recommended_threshold)
        gl.addWidget(self.apply_thr_btn)
        ll.addWidget(g)

        g2 = QGroupBox("Find where the LED shows on screen")
        g2l = QVBoxLayout(g2)
        row2 = QHBoxLayout()
        b4 = QPushButton("Find LED spot")
        b4.setToolTip("Screenshots every display with the LED off and on and marks where the flash changes the picture most")
        b4.clicked.connect(self.find_spot)
        self.marker_btn = QPushButton("Show marker on screen (60 s)")
        self.marker_btn.setEnabled(False)
        self.marker_btn.clicked.connect(self.show_marker)
        row2.addWidget(b4)
        row2.addWidget(self.marker_btn)
        g2l.addLayout(row2)
        self.spot_text = QPlainTextEdit()
        self.spot_text.setReadOnly(True)
        self.spot_text.setMaximumHeight(110)
        g2l.addWidget(self.spot_text)
        ll.addWidget(g2)

        g3 = QGroupBox("Placement guide for the chosen setup and scene")
        g3l = QVBoxLayout(g3)
        self.guide_text = QPlainTextEdit()
        self.guide_text.setReadOnly(True)
        g3l.addWidget(self.guide_text)
        ll.addWidget(g3, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.raw_canvas = Canvas(Figure(figsize=(6, 2.4)))
        rl.addWidget(self.raw_canvas, 1)
        self.spot_image = QLabel("Screenshot with the best sensor position appears here after 'Find LED spot'.")
        self.spot_image.setAlignment(Qt.AlignCenter)
        self.spot_image.setStyleSheet("color:#52514e")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.spot_image)
        rl.addWidget(scroll, 2)
        split.addWidget(right)
        split.setSizes([480, 800])
        self._draw_raw()
        return w

    def _build_analysis_tab(self) -> QWidget:
        w = QWidget()
        split = QSplitter(Qt.Horizontal, w)
        lay = QHBoxLayout(w)
        lay.addWidget(split)

        left = QWidget()
        ll = QVBoxLayout(left)
        row = QHBoxLayout()
        self.an_folder = QLineEdit(DEFAULT_SESSIONS)
        row.addWidget(self.an_folder, 1)
        b = QPushButton("Browse")
        b.clicked.connect(self.choose_analysis_folder)
        row.addWidget(b)
        ll.addLayout(row)
        row2 = QHBoxLayout()
        b2 = QPushButton("Reload")
        b2.clicked.connect(self.load_runs)
        row2.addWidget(b2)
        b3 = QPushButton("Open sample data")
        b3.setToolTip("The ten runs recorded on 21 September 2026 that ship with the app")
        b3.clicked.connect(self.open_sample)
        if not os.path.isdir(SAMPLE_DIR):
            b3.setEnabled(False)
            b3.setToolTip("Sample data is not included in this installation; clone the repository to get it")
        row2.addWidget(b3)
        ll.addLayout(row2)
        ll.addWidget(QLabel("Runs (select one or more; the first selected is the comparison baseline)"))
        self.run_list = QListWidget()
        self.run_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.run_list.itemSelectionChanged.connect(self.update_analysis)
        ll.addWidget(self.run_list, 1)
        row3 = QHBoxLayout()
        self.export_btn = QPushButton("Export report (HTML + PDF)")
        self.export_btn.clicked.connect(self.export)
        row3.addWidget(self.export_btn)
        ll.addLayout(row3)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Figure"))
        self.fig_box = QComboBox()
        self.fig_box.addItems(list(FIGURES))
        self.fig_box.currentIndexChanged.connect(self.update_analysis)
        row4.addWidget(self.fig_box, 1)
        rl.addLayout(row4)
        self.an_canvas = Canvas(Figure(figsize=(6, 3.4)))
        rl.addWidget(self.an_canvas, 3)
        self.stats_table = QTableWidget(0, len(SH))
        self.stats_table.setHorizontalHeaderLabels(SH)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rl.addWidget(self.stats_table, 2)
        self.compare_text = QTextEdit()
        self.compare_text.setReadOnly(True)
        rl.addWidget(self.compare_text, 2)
        split.addWidget(right)
        split.setSizes([380, 900])
        return w

    # ------------------------------------------------------------ settings
    def _restore_settings(self) -> None:
        s = self.settings
        self.folder_edit.setText(s.value("folder", DEFAULT_SESSIONS))
        self.an_folder.setText(s.value("an_folder", self.folder_edit.text()))
        self.host_edit.setText(s.value("host", ""))
        self.thr_spin.setValue(int(s.value("threshold", 30)))
        self.dur_spin.setValue(int(s.value("duration", 120)))
        idx = self.setup_box.findData(s.value("setup", "two-screen"))
        if idx >= 0:
            self.setup_box.setCurrentIndex(idx)
        self.app_box.setCurrentText(s.value("application", APPLICATIONS[0]))
        self.sensorA_edit.setText(s.value("sensorA", ""))
        self.sensorB_edit.setText(s.value("sensorB", ""))
        self._setup_changed()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("folder", self.folder_edit.text())
        s.setValue("an_folder", self.an_folder.text())
        s.setValue("host", self.host_edit.text())
        s.setValue("threshold", self.thr_spin.value())
        s.setValue("duration", self.dur_spin.value())
        s.setValue("setup", self.setup_box.currentData())
        s.setValue("application", self.app_box.currentText())
        s.setValue("sensorA", self.sensorA_edit.text())
        s.setValue("sensorB", self.sensorB_edit.text())
        s.setValue("port", self.port_box.currentData())

    def closeEvent(self, event) -> None:
        self._save_settings()
        if self.measuring:
            self.send("r")
        if self.ping:
            self.ping.stop()
        if self.link:
            self.link.stop()
            self.link.wait(1500)
        super().closeEvent(event)

    # ------------------------------------------------------------ setup / guide
    @property
    def setup(self) -> str:
        return self.setup_box.currentData() or "two-screen"

    def _setup_changed(self) -> None:
        two = self.setup == "two-screen"
        self.host_edit.setEnabled(two)
        self.sensorA_edit.setEnabled(self.setup != "one-screen-b")
        self.sensorB_edit.setEnabled(self.setup != "one-screen-a")
        self._update_guide()

    def _update_guide(self) -> None:
        self.guide_text.setPlainText(placement_guide(self.setup, self.scene_box.currentData() or "dark"))

    # ------------------------------------------------------------ serial
    def refresh_ports(self) -> None:
        current = self.port_box.currentData() or self.settings.value("port", "")
        self.port_box.clear()
        for dev, desc in available_ports():
            self.port_box.addItem(f"{dev}  {desc}", dev)
        idx = self.port_box.findData(current)
        if idx >= 0:
            self.port_box.setCurrentIndex(idx)

    def toggle_connect(self) -> None:
        if self.link:
            if self.measuring:
                self.stop_run()
            self.link.stop()
            self.link.wait(1500)
            self.link = None
            self.fw_ready = False
            self.connect_btn.setText("Connect")
            self.fw_label.setText("Not connected")
            return
        port = self.port_box.currentData()
        if not port:
            QMessageBox.warning(self, "No port", "No serial port selected. Plug in the microcontroller and press Refresh.")
            return
        self.link = SerialLink(port)
        self.link.header_seen.connect(self._on_header)
        self.link.trial_received.connect(self._on_trial)
        self.link.raw_received.connect(self._on_raw)
        self.link.line_received.connect(self._on_line)
        self.link.info_received.connect(lambda s: self.statusBar().showMessage(s, 4000))
        self.link.error.connect(self._on_serial_error)
        self.link.connected.connect(self._on_connected)
        self.fw_ready = False
        self.link.start()
        self.connect_btn.setText("Disconnect")
        self.fw_label.setText(f"Opening {port}, waiting for the firmware header (the board resets on connect)...")

    def send(self, cmd: str) -> None:
        if not self.link:
            self.statusBar().showMessage("Not connected to the microcontroller.", 4000)
            return
        self.link.send(cmd)

    def _on_connected(self, ok: bool) -> None:
        if not ok:
            QTimer.singleShot(300, self._cleanup_link)

    def _cleanup_link(self) -> None:
        """Called after the serial thread has ended on its own (error or unplugged board)."""
        if self.link is not None and not self.link.isRunning():
            self.link = None
            self.fw_ready = False
            self.connect_btn.setText("Connect")
            self.fw_label.setText("Not connected")
            if self.measuring:
                self.stop_run()

    def _on_header(self, line: str) -> None:
        self.fw_label.setText(line.lstrip("# "))
        self.fw_ready = True
        self.send("r")  # idle in raw mode with the LED off until a run starts
        self.statusBar().showMessage("Firmware ready. Board idle with LED off.", 5000)

    def _on_serial_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Serial error", msg)

    def _require_firmware(self) -> bool:
        if not self.link:
            QMessageBox.warning(self, "Not connected", "Connect the microcontroller first.")
            return False
        if not getattr(self, "fw_ready", False):
            QMessageBox.information(self, "Board still starting", "Wait for the firmware header to appear next to the Connect button (about two seconds after connecting).")
            return False
        return True

    def _on_line(self, line: str) -> None:
        if self.measuring:
            self.raw_lines.append(line)

    def _on_raw(self, a: int, b: int) -> None:
        self.raw_label.setText(f"A0: {a}    A1: {b}")
        self.raw_history.append((a, b))
        if self.raw_collect is not None:
            self.raw_collect.append((a, b))
        if self.tabs.currentIndex() == 1:
            self._draw_raw()

    def _draw_raw(self) -> None:
        fig = self.raw_canvas.figure
        fig.clear()
        ax = fig.add_subplot(111)
        xs = range(len(self.raw_history))
        ax.plot(xs, [h[0] for h in self.raw_history], color="#2a78d6", lw=1.5, label="A0 (sensor A)")
        ax.plot(xs, [h[1] for h in self.raw_history], color="#eb6834", lw=1.5, label="A1 (sensor B)")
        ax.set_ylim(0, 1023)
        ax.set_ylabel("ADC counts")
        ax.set_xlabel("Samples (10 per second)")
        ax.grid(True, color="#e1e0d9", lw=0.7)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.legend(loc="upper left", frameon=False, fontsize=8)
        fig.tight_layout()
        self.raw_canvas.draw_idle()

    # ------------------------------------------------------------ measurement
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder for run files", self.folder_edit.text() or DEFAULT_SESSIONS)
        if d:
            self.folder_edit.setText(d)

    def _current_meta(self) -> RunMeta:
        return RunMeta(label=self.label_edit.text().strip(), setup=self.setup, application=self.app_box.currentText().strip(),
                       scene=self.scene_box.currentData() or "dark", sensorA=self.sensorA_edit.text().strip(), sensorB=self.sensorB_edit.text().strip(),
                       threshold=self.thr_spin.value(), channels=channels_for_setup(self.setup), notes=self.notes_edit.text().strip(),
                       started=now_iso(), ping_host=self.host_edit.text().strip() if self.setup == "two-screen" else "",
                       firmware=self.fw_label.text())

    def start_run(self) -> None:
        if not self._require_firmware() or self.measuring:
            return
        self.meta = self._current_meta()
        self.trials = []
        self.raw_lines = []
        self.trial_table.setRowCount(0)
        self.pending_ping_text = None
        self.measuring = True
        self.run_started = dt.datetime.now()
        self.send(f"t{self.meta.threshold}")
        self.send(self.meta.channels)
        self.send("z")  # restart trial numbering (ignored by firmware older than the z command)
        self.send("m")
        if self.meta.ping_host:
            n = max(10, self.dur_spin.value() or 600)
            self.ping = PingWorker(self.meta.ping_host, n)
            self.ping.done.connect(self._on_ping_done)
            self.ping.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.clock.start()
        self.summary_label.setText("")
        fig_live(self.live_canvas.figure, [], self.setup)
        self.live_canvas.draw_idle()
        self.statusBar().showMessage("Run started.", 4000)
        self._save_settings()

    def _tick(self) -> None:
        if not self.measuring:
            return
        el = (dt.datetime.now() - self.run_started).total_seconds()
        dur = self.dur_spin.value()
        self.run_status.setText(f"Measuring: {int(el)} s elapsed, {len(self.trials)} trials" + (f", stops at {dur} s" if dur else ""))
        if dur and el >= dur:
            self.stop_run()

    def stop_run(self) -> None:
        if not self.measuring:
            return
        self.measuring = False
        self.clock.stop()
        self.send("r")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.meta:
            self.meta.ended = now_iso()
        if self.ping and self.ping.isRunning():
            self.run_status.setText("Waiting for the ping batch to finish...")
            self.ping.stop()
        else:
            self._finish_save()

    def _on_ping_done(self, rtts: list, text: str) -> None:
        self.pending_ping_text = text
        self.ping = None
        if not self.measuring:
            self._finish_save()

    def _finish_save(self) -> None:
        if self.meta is None:
            return
        folder = self.folder_edit.text() or DEFAULT_SESSIONS
        stem = f"{self.run_started:%Y%m%d_%H%M%S}_{slug(self.meta.label or self.meta.application or 'run')}"
        run = Run(os.path.join(folder, stem + ".csv"), self.meta, list(self.trials), list(self.raw_lines))
        try:
            save_run(run, self.pending_ping_text)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.run_status.setText(f"Saved {len(self.trials)} trials to {run.path}")
        self.meta = None
        if os.path.abspath(folder) == os.path.abspath(self.an_folder.text() or ""):
            self.load_runs()
        else:
            self.an_folder.setText(folder)
            self.load_runs()

    def _on_trial(self, t) -> None:
        if not self.measuring:
            return
        self.trials.append(t)
        r = self.trial_table.rowCount()
        self.trial_table.insertRow(r)
        vals = [str(t.trial), fmt(t.baseA, 0), fmt(t.baseB, 0), fmt(t.litA, 0), fmt(t.litB, 0), fmt(t.riseA_ms), fmt(t.riseB_ms), fmt(t.fallA_ms), fmt(t.fallB_ms), fmt(t.linkRise_ms), fmt(t.linkFall_ms)]
        for c, v in enumerate(vals):
            self.trial_table.setItem(r, c, QTableWidgetItem(v))
        self.trial_table.scrollToBottom()
        fig_live(self.live_canvas.figure, self.trials, self.setup)
        self.live_canvas.draw_idle()
        run = Run("", self.meta or RunMeta(), self.trials)
        s = run_summary(run)
        bits = []
        for key in measures_for_setup(self.setup):
            v = s.get(key)
            if v:
                bits.append(f"<b>{MEASURES[key]}</b>: {v['mean']:.0f} ms (SD {v['sd']:.0f}, n={v['n']})")
        weak = []
        for ch, base, lit in (("A", t.baseA, t.litA), ("B", t.baseB, t.litB)):
            if (ch == "A" and self.setup == "one-screen-b") or (ch == "B" and self.setup == "one-screen-a"):
                continue
            if lit - base < 4:
                weak.append(ch)
        if weak:
            bits.append(f"<span style='color:#eb6834'>Sensor {' and '.join(weak)}: after 2.5 s with the LED on the screen reads within 4 counts of dark, "
                        f"so the OFF edge cannot be measured. The flash is only transient at this position; slide the sensor to where 'Live, LED on' stays high.</span>")
        self.summary_label.setText("<br>".join(bits))

    # ------------------------------------------------------------ placement
    def check_swing(self) -> None:
        if not self._require_firmware():
            return
        self.verdict_label.setText("Reading dark levels (LED off)...")
        self.send("r")
        QTimer.singleShot(1200, self._swing_collect_dark)

    def _swing_collect_dark(self) -> None:
        self.raw_collect = []
        QTimer.singleShot(1500, self._swing_dark_done)

    def _swing_dark_done(self) -> None:
        self._dark = self._mean_collect()
        self.verdict_label.setText("Reading lit levels (LED on)...")
        self.send("l")
        QTimer.singleShot(1200, self._swing_collect_lit)

    def _swing_collect_lit(self) -> None:
        self.raw_collect = []
        QTimer.singleShot(1500, self._swing_lit_done)

    def _swing_lit_done(self) -> None:
        lit = self._mean_collect()
        self.raw_collect = None
        self.send("r")
        dark = self._dark
        if dark is None or lit is None:
            self.verdict_label.setText("No readings arrived. Is the board connected and the firmware running?")
            return
        parts = []
        thr = []
        for ch, i in (("A", 0), ("B", 1)):
            if (self.setup == "one-screen-a" and ch == "B") or (self.setup == "one-screen-b" and ch == "A"):
                continue
            v = sensor_verdict(dark[i], lit[i])
            parts.append(f"<b>Sensor {ch}</b> dark {v.dark:.0f}, lit {v.lit:.0f}, swing {v.swing:.0f} [{v.status}]: {v.message}")
            if v.status not in ("none", "saturated"):
                thr.append(v.threshold)
        self.recommended_thr = min(thr) if thr else None
        if self.recommended_thr:
            parts.append(f"Recommended threshold: <b>{self.recommended_thr}</b>")
        self.verdict_label.setText("<br>".join(parts))
        self.apply_thr_btn.setEnabled(bool(self.recommended_thr))

    def _mean_collect(self):
        if not self.raw_collect:
            return None
        a = sum(x[0] for x in self.raw_collect) / len(self.raw_collect)
        b = sum(x[1] for x in self.raw_collect) / len(self.raw_collect)
        return (a, b)

    def apply_recommended_threshold(self) -> None:
        if getattr(self, "recommended_thr", None):
            self.thr_spin.setValue(int(self.recommended_thr))
            self.statusBar().showMessage(f"Threshold set to {self.recommended_thr}.", 4000)

    def find_spot(self) -> None:
        if not self._require_firmware():
            return
        self.spot_text.setPlainText("LED off, waiting for the screens to settle...")
        self.send("r")
        QTimer.singleShot(2000, self._spot_grab_off)

    def _spot_grab_off(self) -> None:
        try:
            self.off_shots = grab_monitors()
        except Exception as exc:
            self.spot_text.setPlainText(f"Screenshot failed: {exc}")
            return
        self.spot_text.setPlainText("LED on, waiting for the screens to settle...")
        self.send("l")
        QTimer.singleShot(2000, self._spot_grab_on)

    def _spot_grab_on(self) -> None:
        try:
            self.on_shots = grab_monitors()
        finally:
            self.send("r")
        self.spots = find_led_spots(self.off_shots, self.on_shots)
        if not self.spots:
            self.spot_text.setPlainText("No usable screenshots.")
            return
        lines = [describe_spot(s) for s in self.spots]
        best = self.spots[0]
        lines.insert(0, f"Best position: monitor {best.monitor.index}, {best.pct_x * 100:.0f}% from the left, {best.pct_y * 100:.0f}% from the top. Tape the sensor there.")
        self.spot_text.setPlainText("\n".join(lines))
        shot = next(s for s in self.on_shots if s.monitor.index == best.monitor.index)
        self.spot_image.setPixmap(pil_to_pixmap(annotate(shot, best)))
        self.marker_btn.setEnabled(True)

    def show_marker(self) -> None:
        if not self.spots:
            return
        screen, pt = physical_to_screen_point(self.spots[0])
        self._marker = MarkerOverlay(screen, pt)
        self._marker.show()
        self.statusBar().showMessage("Marker shown for 60 seconds.", 4000)

    # ------------------------------------------------------------ analysis
    def choose_analysis_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder with run files", self.an_folder.text() or DEFAULT_SESSIONS)
        if d:
            self.an_folder.setText(d)
            self.load_runs()

    def open_sample(self) -> None:
        self.an_folder.setText(SAMPLE_DIR)
        self.load_runs()

    def load_runs(self) -> None:
        self.runs = list_runs(self.an_folder.text())
        self.run_list.clear()
        for r in self.runs:
            when = r.meta.started[:16].replace("T", " ") if r.meta.started else ""
            item = QListWidgetItem(f"{r.name}   ({len(r.trials)} trials{', ' + when if when else ''})")
            item.setToolTip(os.path.basename(r.path))
            self.run_list.addItem(item)
        self.update_analysis()

    def selected_runs(self) -> list[Run]:
        rows = sorted(i.row() for i in self.run_list.selectedIndexes())
        return [self.runs[i] for i in rows]

    def update_analysis(self) -> None:
        runs = self.selected_runs()
        self.stats_table.setRowCount(0)
        for row in summary_rows(runs):
            r = self.stats_table.rowCount()
            self.stats_table.insertRow(r)
            for c, v in enumerate(row):
                self.stats_table.setItem(r, c, QTableWidgetItem(str(v).replace("&amp;", "&")))
        fig = self.an_canvas.figure
        if runs:
            FIGURES[self.fig_box.currentText()](fig, runs)
        else:
            fig.clear()
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, "Select runs on the left", ha="center", va="center", color="#52514e", transform=ax.transAxes)
            ax.set_axis_off()
        self.an_canvas.draw_idle()
        self.compare_text.setHtml(self._comparison_html(runs))

    def _comparison_html(self, runs: list[Run]) -> str:
        if not runs:
            return ""
        out = []
        base = runs[0]
        if len(runs) >= 2:
            out.append(f"<h4>Differences relative to {base.name} (Welch)</h4><ul>")
            for r in runs[1:]:
                for c in compare_runs(r, base):
                    out.append(f"<li><b>{r.name}</b>, {c['label']}: {c['diff']:+.1f} ms (95% CI {c['lo']:+.0f} to {c['hi']:+.0f}, p = {c['p']:.2g}, n = {c['na']} vs {c['nb']})</li>")
            out.append("</ul>")
        out.append("<h4>Edge symmetry, fall minus rise, paired within trials</h4><ul>")
        for r in runs:
            for ch, e in edge_symmetry(r).items():
                if e:
                    out.append(f"<li><b>{r.name}</b>, sensor {ch}: {e['diff']:+.1f} ms (SD {e['sd']:.1f}, n = {e['n']}, p = {e['p']:.2g})</li>")
        out.append("</ul>")
        pings = [(r, ping_summary(r.ping)) for r in runs if r.ping]
        if pings:
            out.append("<h4>Network round-trip time</h4><ul>")
            for r, p in pings:
                out.append(f"<li><b>{r.name}</b> to {r.meta.ping_host}: median {p['median']:.0f} ms, p95 {p['p95']:.0f} ms, max {p['max']} ms, {p['spikes']} replies above 100 ms of {p['n']}</li>")
            out.append("</ul>")
        return "".join(out)

    def export(self) -> None:
        runs = self.selected_runs()
        if not runs:
            QMessageBox.information(self, "Nothing selected", "Select one or more runs first.")
            return
        default = os.path.join(self.an_folder.text() or DEFAULT_SESSIONS, f"report_{dt.datetime.now():%Y%m%d_%H%M}.html")
        path, _ = QFileDialog.getSaveFileName(self, "Save report as", default, "HTML report (*.html)")
        if not path:
            return
        html_path, pdf_path = export_report(runs, path)
        msg = f"HTML report written to:\n{html_path}"
        msg += f"\n\nPDF written to:\n{pdf_path}" if pdf_path else "\n\nNo Chromium-based browser found for PDF printing; open the HTML in a browser and print it, or set CHROME_PATH."
        QMessageBox.information(self, "Report exported", msg)


def main() -> None:
    if os.name == "nt":
        try:  # let Windows show the app's own icon in the taskbar instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EndoLatency.EndoLatency")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("EndoLatency")
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
