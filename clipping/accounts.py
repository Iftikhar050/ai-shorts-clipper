"""
clipping.accounts — Target-account definitions for clip classification.

Each clip is classified into one of *your* destination accounts (channels /
pages) based on its angle. Accounts are loaded from a JSON file so they are
not hard-coded in the prompt:

    {
      "accounts": {
        "Sports": {
          "handle": "@yourname.sports",
          "angle_desc": "Match highlights, records, player moments, rivalries.",
          "bio": "Daily cricket and football highlights."
        }
      },
      "rules": [
        "Player interviews about money or contracts go to Sports, not Business."
      ]
    }

Only "angle_desc" is required per account. "handle" defaults to the account
key and "bio" to an empty string. "rules" is optional.

The account key becomes ``tipe_akun`` and the handle becomes ``akun_tujuan``
in the AI output, so existing metadata/uploader field names are unchanged.
"""

import json
import os

ACCOUNTS_FILENAME = "accounts.json"

DEFAULT_ACCOUNTS = {
    "General": {
        "akun_tujuan": "General",
        "angle_desc": "Any clip. Use this account for every clip.",
        "bio": "",
    }
}


class AccountsError(ValueError):
    """Raised when an accounts file is missing or malformed."""


def _normalize(key: str, data) -> dict:
    if not isinstance(data, dict):
        raise AccountsError(f"Account '{key}' must be an object.")
    angle = data.get("angle_desc")
    if not isinstance(angle, str) or not angle.strip():
        raise AccountsError(f"Account '{key}' needs a non-empty \"angle_desc\".")
    handle = data.get("handle", data.get("akun_tujuan", key))
    bio = data.get("bio", "")
    if not isinstance(handle, str) or not handle.strip():
        raise AccountsError(f"Account '{key}' has an empty \"handle\".")
    if not isinstance(bio, str):
        raise AccountsError(f"Account '{key}' \"bio\" must be text.")
    return {"akun_tujuan": handle.strip(), "angle_desc": angle.strip(), "bio": bio.strip()}


def load_accounts(path: str) -> tuple[dict, list[str]]:
    """Load and validate an accounts file. Returns ``(accounts, rules)``."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise AccountsError(f"Accounts file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise AccountsError(f"Accounts file is not valid JSON ({path}): {exc}") from None

    if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), dict):
        raise AccountsError(f"{path}: expected an object with an \"accounts\" object.")
    if not raw["accounts"]:
        raise AccountsError(f"{path}: \"accounts\" must contain at least one account.")

    accounts = {}
    for key, data in raw["accounts"].items():
        if not isinstance(key, str) or not key.strip():
            raise AccountsError(f"{path}: account names must be non-empty text.")
        accounts[key.strip()] = _normalize(key, data)

    rules = raw.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        raise AccountsError(f"{path}: \"rules\" must be a list of text lines.")
    return accounts, [r.strip() for r in rules if r.strip()]


def resolve_accounts(accounts_file: str | None, base_dir: str, classify: bool = True) -> tuple[dict, list[str], str]:
    """Pick the accounts to classify into.

    Returns ``(accounts, rules, source)`` where *source* describes where the
    accounts came from (for the run banner).

    - classification off            -> built-in General account
    - explicit ``--accounts-file``  -> that file (must exist)
    - ``accounts.json`` in base_dir -> that file
    - otherwise                     -> built-in General account
    """
    if not classify:
        return dict(DEFAULT_ACCOUNTS), [], "off"
    if accounts_file:
        accounts, rules = load_accounts(accounts_file)
        return accounts, rules, accounts_file
    default_path = os.path.join(base_dir, ACCOUNTS_FILENAME)
    if os.path.isfile(default_path):
        accounts, rules = load_accounts(default_path)
        return accounts, rules, default_path
    return dict(DEFAULT_ACCOUNTS), [], "built-in (General)"
