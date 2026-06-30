"""
FileReach bootstrap / launcher.

What this does, in order:
  1. Ensures it is running with Administrator rights (single UAC prompt).
     This is required so the search can read every folder on the PC, including
     system folders and other users' folders, without per-folder permission nagging.
  2. Creates a private virtual environment (.venv) next to this script.
  3. Installs dependencies from requirements.txt (only if missing).
  4. Launches app.py and opens the browser.

Run it with:   python run.py        (or double-click start.bat on Windows)
"""

import os
import sys
import subprocess
import platform


HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
IS_WIN = platform.system() == "Windows"
PY = os.path.join(VENV, "Scripts", "python.exe") if IS_WIN else os.path.join(VENV, "bin", "python")


# --------------------------------------------------------------------------- #
#  Step 1 — Administrator elevation (Windows only)
# --------------------------------------------------------------------------- #
def is_admin():
    if not IS_WIN:
        return True
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    """Re-launch THIS script elevated. This is the ONE UAC prompt the user sees."""
    if not IS_WIN:
        return
    import ctypes
    params = " ".join(f'"{a}"' for a in sys.argv)
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{os.path.abspath(__file__)}" {params}'.strip(),
        None, 1,
    )
    if rc <= 32:
        print("Administrator permission was declined. FileReach needs it to read all folders.")
        print("Re-run and click 'Yes' on the permission prompt.")
        input("Press Enter to exit…")
        sys.exit(0)
    sys.exit(0)  # the elevated copy takes over


# --------------------------------------------------------------------------- #
#  Step 2/3 — virtualenv + deps
# --------------------------------------------------------------------------- #
def ensure_venv():
    if os.path.isfile(PY):
        return
    print("› Creating virtual environment…")
    subprocess.check_call([sys.executable, "-m", "venv", VENV])


def ensure_deps():
    marker = os.path.join(VENV, ".deps_installed")
    # Quick check: is flask importable?
    probe = subprocess.run(
        [PY, "-c", "import flask, PIL"],
        capture_output=True,
    )
    if probe.returncode == 0 and os.path.isfile(marker):
        return
    print("› Installing dependencies (first run only)…")
    req = os.path.join(HERE, "requirements.txt")
    subprocess.check_call([PY, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([PY, "-m", "pip", "install", "-r", req])
    with open(marker, "w") as f:
        f.write("ok")


# --------------------------------------------------------------------------- #
#  Step 4 — run
# --------------------------------------------------------------------------- #
def run():
    os.chdir(HERE)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    try:
        subprocess.check_call([PY, os.path.join(HERE, "app.py")])
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    if IS_WIN and not is_admin():
        print("Requesting permission to access all folders (this prompt appears only once)…")
        relaunch_as_admin()
    print("=" * 56)
    print("  FileReach — preparing your environment")
    print("=" * 56)
    ensure_venv()
    ensure_deps()
    print("› Launching FileReach…")
    run()


if __name__ == "__main__":
    main()
