"""Build a self-contained EndoLatency folder with PyInstaller.

    pip install pyinstaller
    python build_exe.py

Output: dist/EndoLatency/  (run EndoLatency.exe inside it; zip the folder to distribute).
Works on Windows, Linux and macOS; the result is for the platform it is built on.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sep = ";" if os.name == "nt" else ":"
icon = os.path.join("endolatency", "assets", "icon.ico")
cmd = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "EndoLatency",
    "--icon", icon,
    "--add-data", f"endolatency{os.sep}assets{sep}endolatency{os.sep}assets",
    "--add-data", f"firmware{sep}firmware",
    "--collect-submodules", "endolatency",
    "--hidden-import", "serial.tools.list_ports",
]
# Packages that optional import paths in matplotlib/PIL/scipy can drag in from a busy Python
# environment but that the app never uses. Excluding them keeps the bundle small.
for mod in ("tkinter", "torch", "torchvision", "torchaudio", "pyarrow", "av", "onnxruntime", "botocore", "boto3", "pandas", "sklearn",
            "cv2", "tensorflow", "IPython", "jupyter", "notebook", "PyQt5", "PyQt6", "wx", "pytest", "sympy", "numba", "h5py", "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtBluetooth", "PySide6.QtNfc",
            "PySide6.QtRemoteObjects", "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtTextToSpeech", "PySide6.QtWebSockets", "PySide6.QtWebChannel"):
    cmd += ["--exclude-module", mod]
cmd.append("launcher.py")
print(" ".join(cmd))
subprocess.run(cmd, check=True)
out = os.path.join("dist", "EndoLatency")
if os.path.isdir(out):
    shutil.copy("README.md", os.path.join(out, "README.md"))
    zip_base = os.path.join("dist", f"EndoLatency-{sys.platform}")
    shutil.make_archive(zip_base, "zip", "dist", "EndoLatency")
    print("built", out, "and", zip_base + ".zip")
