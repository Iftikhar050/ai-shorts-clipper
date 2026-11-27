"""YouTube cookies passed to yt-dlp, using only synthetic cookies."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

YOUTUBE_URL = "https://www.youtube.com/watch?v=test"
FILE = b"# Netscape HTTP Cookie File\n#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsynthetic-value\n"


def test_subtitle_and_media_downloads_get_independent_cookie_streams(monkeypatch, tmp_path):
    destination = tmp_path / "source.mp4"
    received = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            return {}
        def download(self, urls):
            jar = self.opts["cookiefile"]
            received.append(jar.read())
            jar.write("updated by fake yt-dlp")
            if self.opts.get("skip_download"):
                destination.with_suffix(".en.json3").touch()
            else:
                destination.touch()

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=object))
    source = Path(__file__).resolve().parents[1] / "clipping/engine.py"
    spec = importlib.util.spec_from_file_location("_cookie_download_engine", source)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.download_video(YOUTUBE_URL, str(destination), use_dlp_subs=True, yt_cookies=FILE)
    assert received == [FILE.decode(), FILE.decode()]
    assert not list(tmp_path.glob("*.txt"))
