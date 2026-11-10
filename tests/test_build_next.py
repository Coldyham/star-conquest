"""The HTML build's game zip: everything the worker imports, and no pygame."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile

from tools import build_next


def test_the_zip_is_deterministic_and_named_by_its_bytes():
    data = build_next.game_zip()
    assert build_next.game_zip() == data
    assert build_next.zip_name(data) == build_next.zip_name(build_next.game_zip())
    assert f'GAME_ZIP = "py/{build_next.zip_name(data)}"' in build_next.build_info(f"py/{build_next.zip_name(data)}")


def test_the_zip_ships_no_shell_module():
    names = zipfile.ZipFile(io.BytesIO(build_next.game_zip())).namelist()
    shipped = {n.split("/")[1][:-3] for n in names if n.startswith("starconquest/") and n.endswith(".py")}
    assert not shipped & build_next.SHELL
    assert {"session", "app", "engine", "hostio", "serial"} <= shipped
    assert "starconquest/assets/gauntlets/tutorial.json" in names
    assert any(n.startswith("models/") for n in names)


def test_the_unpacked_zip_plays_a_turn_with_pygame_unimportable(tmp_path):
    zipfile.ZipFile(io.BytesIO(build_next.game_zip())).extractall(tmp_path)
    script = f"""
import json, sys
sys.modules["pygame"] = None          # importing it now raises
sys.path.insert(0, {str(tmp_path)!r})
from starconquest.session import Session
s = Session()
call = lambda op, **a: json.loads(s.handle(json.dumps({{"id": 1, "op": op, "args": a}})))
assert call("boot", page={{}}, store={{}})["ok"]
assert call("game.start", settings={{"players": 2, "nodes": 12, "autoplay": True}}, seed=3)["ok"]
reply = call("turn.end")
assert reply["ok"], reply
import starconquest
print(json.dumps({{"turn": reply["result"]["board"]["turn"], "from": starconquest.__file__}}))
"""
    out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, cwd=tmp_path,
                         check=False)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["turn"] == 1 and got["from"].startswith(str(tmp_path))


def test_only_the_shell_fonts_are_served():
    assert all(build_next.font_path(f).is_file() for f in build_next.FONTS)
    assert not build_next.font_path("../main.py").exists()
    assert not build_next.font_path("gauntlets/tutorial.json").exists()


def test_the_launcher_puts_the_menus_flags_in_the_address(monkeypatch):
    """`main.py --html` hands its flags to `serve.main`, which opens the page
    with them as its query (`js/play.mjs`'s ``setup``), so they pre-fill the
    menu as they pre-fill pygame's."""
    from starconquest import main as game_main
    from starconquest import serve

    opened, started = [], []
    monkeypatch.setattr(serve, "start", lambda port: started.append(port) or type("S", (), {"shutdown": lambda s: None})())
    monkeypatch.setattr(serve.webbrowser, "open", opened.append)
    monkeypatch.setattr(serve.threading.Event, "wait", lambda self: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(sys, "argv", ["main.py", "--html", "--players", "4", "--seed", "42", "--no-menu"])
    game_main.run()
    assert started == [serve.PORT]
    assert opened == ["http://127.0.0.1:8000/next/?players=4&seed=42&go"]
    monkeypatch.setattr(sys, "argv", ["main.py", "--html", "--autoplay", "--mode", "symmetric"])
    game_main.run()
    assert opened[-1] == "http://127.0.0.1:8000/next/?mode=symmetric&autoplay"
