"""Keep tests away from the user's real credentials (.env)."""

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
