# External bots

A bot here is a **program**, not a Python file: it speaks the JSON protocol in
[`docs/bot-api.md`](../docs/bot-api.md) over stdin/stdout, so it can be written
in any language. Each one is a manifest, `<name>.bot.json`, naming the command
to run:

```json
{
  "name": "rusherwire",
  "cmd": ["python3", "main.py"],
  "cwd": "rusherwire",
  "protocol": 1,
  "version": "1.0.0",
  "budget_ms": 150,
  "budget_scale": 100
}
```

`version` matters more than it looks: `tools/bot_replay.py`'s `engine_rev()`
digests `starconquest/` and `models/*.py`, and a compiled binary is in neither —
so a leaderboard row has to carry this, or recompiling a bot serves its stale
cached score forever.

They run in the ladder, not in the game:

```sh
uv run python -m tests.sim --external --ladder --trials 20
uv run python -m tests.sim --external --ai rusherwire marshal --trials 50
```

**Not in the app, and not in the browser.** `tools/build_web.sh` stages
`starconquest/` and `models/` only, and the web build is CPython on WASM —
single threaded, unable to fork a child process at all. The in-app Strategy
dropdown stays Python (`models/`, see [`models/README.md`](../models/README.md));
this folder is for competition.

`rusherwire/` is the worked example: `models/rusherplus.py` ported to read the
payload and nothing else, which is what `tests/test_botio.py` compares
order-for-order against the original.
