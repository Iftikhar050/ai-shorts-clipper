"""
run_app.py — Start the AI Shorts Clipper web app on this computer.

    python run_app.py                       # opens http://127.0.0.1:8765
    python run_app.py --port 9000 --no-browser

Any `python` works: if the project's .venv exists, the app restarts itself
with that interpreter (it has the pipeline's packages).

The app listens on 127.0.0.1 only (this PC). It has no login, so don't
expose it to a network.
"""

import argparse
import os
import subprocess
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


def _venv_python() -> str | None:
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        path = os.path.join(HERE, ".venv", *rel)
        if os.path.isfile(path):
            return path
    return None


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def main() -> None:
    venv = _venv_python()
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if venv and not in_venv and not _same_file(sys.executable, venv) and not os.environ.get("ASC_NO_REEXEC"):
        # Started with a global Python: re-run with the project's .venv.
        env = {**os.environ, "ASC_NO_REEXEC": "1"}
        try:
            sys.exit(subprocess.call([venv, os.path.abspath(__file__), *sys.argv[1:]], env=env))
        except KeyboardInterrupt:
            sys.exit(0)

    p = argparse.ArgumentParser(description="AI Shorts Clipper web app")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    os.chdir(HERE)  # works from any folder
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import uvicorn

        from app.server import create_app
    except ImportError as exc:
        sys.exit(f"Missing package ({exc.name}). Install the requirements first:\n"
                 f"  {sys.executable} -m pip install -r requirements-cpu.txt")

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  AI Shorts Clipper  ->  {url}\n  (keep this window open; Ctrl+C to stop)\n", flush=True)
    try:
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    except OSError as exc:
        sys.exit(f"Could not start on port {args.port} ({exc}). Is the app already running? "
                 f"Open {url} or use --port 9000.")


if __name__ == "__main__":
    main()
