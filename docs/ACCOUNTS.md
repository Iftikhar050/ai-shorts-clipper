# Target accounts

Every clip is classified into one of **your** destination accounts (a channel,
page or profile) based on its angle. The result is saved in
`klasifikasi_akun` (`tipe_akun` = account name, `akun_tujuan` = handle) in
`gemini_response.json` and `metadata_preview.json`.

## Set up

1. Copy `accounts.example.json` to `accounts.json` in the folder you run
   `main.py` from.
2. Edit the accounts:

| Field | Required | Meaning |
|---|---|---|
| account name (the key) | yes | Short category name, e.g. `Sports` |
| `angle_desc` | yes | When a clip belongs here. Describe the *angle*, not just the topic. |
| `handle` | no | Your channel/page handle. Defaults to the account name. |
| `bio` | no | Account bio, gives the AI extra context. |

3. Optional `rules`: tie-breakers for clips that could fit more than one
   account, e.g. "An athlete talking about sleep goes to Science, not Sports."

## Which accounts are used

| Situation | Accounts |
|---|---|
| `--accounts-file path.json` | That file (the run stops if it is missing or invalid) |
| `accounts.json` exists in the working directory | That file |
| Neither | One built-in `General` account |
| `--no-account-classify` | Classification skipped, every clip is `General` (shorter prompt) |

The run banner shows which accounts were loaded:

```
   Accounts    : Sports, Science, General  [E:\...\accounts.json]
```
