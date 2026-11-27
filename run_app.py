"""
run_app.py — Start the AI Shorts Clipper web app on this computer.

    .\\.venv\\Scripts\\python.exe run_app.py          # opens http://127.0.0.1:8765
    .\\.venv\\Scripts\\python.exe run_app.py --port 9000 --no-browser

The app listens on 127.0.0.1 only (this PC). It has no login, so don't
expose it to a network.
"""

import argparse
import threading
import webbrowser


def main() -> None:
    p = argparse.ArgumentParser(description="AI Shorts Clipper web app")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    import uvicorn

    from app.server import create_app

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  AI Shorts Clipper  →  {url}\n  (Ctrl+C to stop)\n")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
