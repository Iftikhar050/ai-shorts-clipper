# clipping — AI Auto-Clipper & Teaser Generator

import os as _os

# Hugging Face's Xet transfer can stall at 0 bytes on some networks (seen on
# Windows while downloading the Whisper model). Classic HTTP downloads are
# reliable. Must be set before huggingface_hub is imported; users can still
# override it with HF_HUB_DISABLE_XET=0.
_os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
_os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
