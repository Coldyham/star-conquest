"""Star Conquest — the pygame main loop, launched by the root ``main.py`` shim.

    uv run python main.py                       # opens the setup menu
    uv run python main.py --players 4           # menu pre-filled from CLI args
    uv run python main.py --no-menu --autoplay  # skip the menu (scriptable demo)

CLI args pre-fill the setup menu; --no-menu starts a game straight from them.
The loop owns transient view-state and scene wiring only; all rules live in the
pure core (engine/combat/mapgen/ai), and all drawing lives in render/menu.
"""

from __future__ import annotations

import argparse
import asyncio

import pygame

from starconquest import (
    ai,
    app,
    config,
    daily,
    engine,
    gauntletgen,
    mapgen,
    mapmaker,
    menu,
    paths,
    pbp,
    render,
    replay,
    setupmenu,
    share,
    softkeyboard,
    turnfilm,
    viewstate,
    webstore,
)
from starconquest import input as game_input
from starconquest import run as gauntlet_run
from starconquest.app import (
    AUTOPLAY_MS,
    GAUNTLET_GEN_BUDGET_S,
    HINT_STRATEGY,
    PBP_MISSING_MSG,
    PBP_REFUSED_MSG,
    PLAY_MS,
    WATCH_MISSING_MSG,
    WATCH_OUTDATED_MSG,
    WATCH_UNREACHABLE_MSG,
    WATCH_UNREADABLE_MSG,
    _apply_shared_link,
    _begin_game,
    _carry_into,
    _decode_log,
    _next_history_film,
    apply_rewind,
    apply_toggle_play,
    build_view,
    campaign_tick,
    carry_autoplay,
    copy_daily_result,
    gauntlet_request,
    hold_history_marks,
    is_log_path,
    land_film,
    land_par,
    land_post_name,
    land_quick_post,
    log_settings,
    open_history,
    open_map_page,
    open_replay,
    pbp_request,
    pbp_spectate_request,
    post_to_leaderboard,
    press_hint,
    quick_post,
    read_game_log,
    replay_request,
    resolve_turn,
    resume_game,
    share_challenge,
    spec_request,
    stage_ui,
    stamp_campaign,
    start_game,
    step_delay,
    take_hint,
    toggle_fast_forward,
)
from starconquest.menu import MenuState
from starconquest.pbpclient import PbpClient
from starconquest.model import GameState
from starconquest.replay import GameLog
from starconquest.settings import Settings, resolve_seed
from starconquest.viewstate import Ui

# Web only: how often the loop re-reads the browser viewport to refit the canvas.
# A poll rather than an event because pygbag swallows the page's resize itself
# (it only restyles the canvas), and throttled because every read crosses into JS.
WEB_VIEWPORT_POLL_MS = 250


# Shown on the menu when a confirmed quit couldn't actually close the app.
CANT_CLOSE_MSG = setupmenu.CANT_CLOSE_MSG


def leave_app() -> bool:
    """Act on a confirmed quit. True if the main loop should end and the process go.

    Off the web that is always right. In the browser there is nothing to exit *to*:
    ending the loop runs ``pygame.quit()``, which destroys the canvas and leaves the
    player staring at a blank page that only a force-close escapes — so we ask the
    browser to close the window (which an installed PWA often can, and a plain tab
    usually can't) and report that we are still running. The caller then drops back
    to the setup menu with ``CANT_CLOSE_MSG`` rather than tearing the display down.
    """
    if not paths.is_web():
        return True
    webstore.close_window()
    return False


def show_note(ms: MenuState, settings: Settings | None, note: app.MenuNote) -> None:
    """Show an `app.MenuNote` on the setup menu, in its field order."""
    if note.status is not None:
        menu.set_status(ms, *note.status)
    if note.gauntlet is not None:
        assert settings is not None
        menu.open_gauntlet(ms, settings, note.gauntlet)
    if note.said is not None:
        menu.say_gauntlet(ms, *note.said)


def open_run(ms: MenuState, settings: Settings, fresh: bool
             ) -> tuple[GameState, Ui, GameLog, int] | None:
    """`app.open_run` on the menu's picked gauntlet, its word shown in the list."""
    opened, note = app.open_run(settings, fresh, menu.picked_gauntlet(ms), ms.gauntlets,
                                lambda g, sid: menu.enter_stage(ms, settings, g, sid))
    show_note(ms, settings, note)
    return opened


def open_linked_gauntlet(menu_state: MenuState, settings: Settings,
                         gid: str | None, data: dict | None) -> pbp.Request | None:
    """`app.open_linked_gauntlet`, shown on the menu."""
    pending, note = app.open_linked_gauntlet(gid, data)
    show_note(menu_state, settings, note)
    return pending


def land_gauntlet(menu_state: MenuState, settings: Settings, state: str, body: str) -> None:
    """`app.land_gauntlet`, shown on the menu."""
    show_note(menu_state, settings, app.land_gauntlet(state, body))


def open_daily(menu_state: MenuState, day: str = "", kind: str = daily.BOARD) -> pbp.Request | None:
    """`app.open_daily`, shown on the menu."""
    pending, note = app.open_daily(day, kind)
    show_note(menu_state, None, note)
    return pending


def land_daily(menu_state: MenuState, settings: Settings, state: str, body: str,
               kind: str = daily.BOARD) -> None:
    """`app.land_daily`, shown on the menu."""
    show_note(menu_state, settings, app.land_daily(settings, state, body, kind))


def start_generation(menu_state: MenuState, fragment: str) -> gauntletgen.Job | None:
    """`app.start_generation`, shown on the menu."""
    job, note = app.start_generation(fragment)
    show_note(menu_state, None, note)
    return job


def pump_generation(menu_state: MenuState, settings: Settings,
                    job: gauntletgen.Job) -> gauntletgen.Job | None:
    """`app.pump_generation` for one frame's budget, shown on the menu."""
    job_left, note = app.pump_generation(settings, job, GAUNTLET_GEN_BUDGET_S)
    show_note(menu_state, settings, note)
    return job_left


def ask_listing(menu_state: MenuState, job: gauntletgen.Job) -> tuple[str, str, pbp.Request] | None:
    """`app.ask_listing`, shown on the menu."""
    asked, note = app.ask_listing(job)
    show_note(menu_state, None, note)
    return asked


def land_listing(menu_state: MenuState, gid: str, name: str, state: str) -> None:
    """`app.land_listing`, shown on the menu."""
    show_note(menu_state, None, app.land_listing(gid, name, state))
PBP_ACTIVITY_EVENTS = frozenset((
    pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEWHEEL, pygame.KEYDOWN,
    pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.WINDOWFOCUSGAINED))


async def _kick_web_resize() -> None:
    """Web only: fire a synthetic browser resize shortly after being scheduled.

    Called twice — once at boot, once off the main loop's first input event (see
    the comments at both call sites) — always as a fire-and-forget background
    task so it never holds up a frame.
    """
    await asyncio.sleep(0.3)
    webstore.trigger_resize()


def ui_scale_for(size: tuple[int, int], touch: bool, pct: int,
                 css_px: float | None = None) -> float:
    """The UI scale for a surface of ``size``: the baseline design size fitted
    into it (never below 1), then the touch boost or, off touch, the player's UI
    size. Touch offers no UI size control, so a stored one is ignored there.

    ``css_px`` is a touch browser's device pixels per CSS pixel (`web_css_px`).
    With it the fit is taken in CSS pixels and one baseline pixel is up to
    ``config.TOUCH_CSS_SCALE`` CSS pixels, less where that would leave fewer
    than ``TOUCH_MIN_W`` x ``TOUCH_MIN_H`` baseline units: a phone's framebuffer
    is the size of a desktop's, so fitting the baseline into its device pixels
    drew a desktop's worth of UI at desktop density on a screen a sixth the
    size, while a whole CSS pixel each left a phone under its address bar too
    little room to lay out in."""
    sw, sh = size
    if touch and css_px:
        cw, ch = sw / css_px, sh / css_px
        fit = max(1.0, min(cw / config.BASE_SCREEN_W, ch / config.BASE_SCREEN_H))
        room = min(cw / config.TOUCH_MIN_W, ch / config.TOUCH_MIN_H)
        return css_px * fit * max(config.TOUCH_CSS_MIN, min(config.TOUCH_CSS_SCALE, room))
    fit = max(1.0, min(sw / config.BASE_SCREEN_W, sh / config.BASE_SCREEN_H))
    return fit * config.TOUCH_UI_SCALE if touch else fit * pct / 100


# Device pixels per CSS pixel of the web canvas, as last fitted (None off the web
# or before the first fit). `web_framebuffer` caps the framebuffer at a pixel
# budget, so this is the ratio it chose, not always the browser's DPR.
web_css_px: float | None = None


def web_framebuffer(css_w: int, css_h: int, dpr: float) -> tuple[int, int]:
    """The canvas framebuffer for a canvas shown at ``css_w`` x ``css_h``.

    The box's own shape, at its device resolution (so nothing is scaled and
    text stays crisp) but never more pixels than ``WEB_FB_W`` x ``WEB_FB_H``,
    which bounds the per-frame draw cost on a big high-DPI screen. pygbag fits
    the canvas to the page keeping the framebuffer's aspect, so a fixed 16:9
    surface was letterboxed in any other window and never reflowed at all; this
    one fills it, and a resize or a rotation changes the surface size the main
    loop already reflows on.
    """
    budget = (config.WEB_FB_W * config.WEB_FB_H / (css_w * css_h)) ** 0.5
    k = min(max(dpr, 1.0), budget)
    return max(1, round(css_w * k)), max(1, round(css_h * k))


def fit_web_canvas() -> bool:
    """Web only: resize the framebuffer to the browser viewport if it has moved.

    Returns True if it changed. The new surface is picked up by the loop's reflow.
    The canvas's on-screen box is ours, not pygbag's (``webstore.viewport``), so
    nothing else needs telling.
    """
    global web_css_px
    view = webstore.viewport()
    if view is None:
        return False
    size = web_framebuffer(*view)
    web_css_px = size[0] / max(1, view[0])
    screen = pygame.display.get_surface()
    if screen is not None and screen.get_size() == size:
        return False
    pygame.display.set_mode(size)
    return True


def ui_size_key(event, pct: int) -> int | None:
    """The UI size a Ctrl +/−/0 press asks for, or None if ``event`` isn't one."""
    if not event.mod & pygame.KMOD_CTRL:
        return None
    if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
        return webstore.step_ui_size(pct, +1)
    if event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
        return webstore.step_ui_size(pct, -1)
    if event.key in (pygame.K_0, pygame.K_KP_0):
        return 100
    return None


def fake_phone(spec: str) -> tuple[tuple[int, int], float]:
    """Desktop only, for development: the window and device pixel ratio that
    stand in for a touch browser's viewport of ``spec`` ("WxH" CSS px, or
    "WxH@DPR"). Sets `web_css_px` to that ratio, so the scale and the compact
    layout come out as they would on that phone. With no DPR given, the largest
    up to 2 that keeps the window on the desktop."""
    global web_css_px
    size, _, ratio = spec.lower().partition("@")
    css_w, css_h = (int(n) for n in size.split("x"))
    if ratio:
        dpr = float(ratio)
    else:
        desk_w, desk_h = (pygame.display.get_desktop_sizes() or [(1920, 1080)])[0]
        dpr = min(2.0, 0.9 * desk_w / css_w, 0.85 * desk_h / css_h)
    web_css_px = dpr
    return (round(css_w * dpr), round(css_h * dpr)), dpr


async def main() -> None:
    ap = argparse.ArgumentParser(description="Star Conquest")
    ap.add_argument("--seed", type=int, default=None, help="map seed (random if omitted)")
    ap.add_argument("--mode", choices=["random", "symmetric"], default="random")
    ap.add_argument("--layout", choices=mapgen.SYMMETRIC_LAYOUTS, default=mapgen.SYMMETRIC_LAYOUTS[0],
                    help="how a symmetric map's sectors are joined (ignored on a random map)")
    ap.add_argument("--players", type=int, default=config.DEFAULT_PLAYERS)
    ap.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    ap.add_argument("--autoplay", action="store_true", help="AI plays all seats")
    ap.add_argument("--watch", default="", metavar="MATCH_ID|FILE",
                    help="open a replay in history review: a posted match id (the "
                         "desktop equivalent of a #log=<id> link) or a saved games/ file")
    ap.add_argument("--settings-from", default="", metavar="FILE",
                    help="pre-fill the menu with a saved games/ file's setup and seed "
                         "(replaces the map flags above)")
    ap.add_argument("--match", default="", metavar="MATCH:TOKEN",
                    help="open a play-by-post seat (the desktop equivalent of a "
                         "#pbp=<match>:<token> link)")
    ap.add_argument("--spectate", default="", metavar="MATCH",
                    help="watch a play-by-post match from no seat (the desktop "
                         "equivalent of a #spectate=<match> link)")
    ap.add_argument("--gauntlet-spec", default="", metavar="SPEC.json",
                    help="generate a gauntlet from a spec file (the desktop equivalent "
                         "of a #gauntlet-spec= link)")
    ap.add_argument("--no-menu", action="store_true",
                    help="skip the setup menu and start straight away with these args")
    ap.add_argument("--phone", nargs="?", const="822x316", default="", metavar="WxH[@DPR]",
                    help="development: lay out as a touch browser with this CSS px viewport "
                         "(default 822x316, a landscape phone under its address bar)")
    # On Android the launcher may pass argv the parser doesn't recognise, and a
    # parse error would sys.exit() before anything renders. There are no CLI args
    # on a phone anyway, so force defaults there.
    args = ap.parse_args([] if paths.is_android() else None)

    settings = Settings.from_args(args)
    if args.settings_from:
        try:
            settings.copy_from(log_settings(read_game_log(args.settings_from)))
        except ValueError as exc:
            ap.error(str(exc))
        settings.autoplay = settings.autoplay or args.autoplay
    _apply_shared_link(settings)   # web only: pre-fill from a #<token> in the URL
    ai.load_models()          # register any drop-in models/ strategies up front

    pygame.init()
    if paths.is_android():
        # Let SDL create a surface at the real device resolution; passing a fixed
        # size can misbehave on mobile. The loop reads the actual size below.
        pygame.display.set_mode((0, 0))
    elif paths.is_web():
        # Start at pygbag's own canvas size (the build's --width/--height), then
        # take the viewport's shape: a framebuffer of any other aspect is
        # letterboxed by pygbag rather than filling the window (see
        # `web_framebuffer`). The loop keeps refitting it as the page resizes.
        pygame.display.set_mode((config.WEB_FB_W, config.WEB_FB_H))
        fit_web_canvas()
        # The canvas can land squashed to the wrong aspect ratio on first paint
        # (pygbag's own resize handler is what fits it, and that only runs on a
        # genuine `resize` event, never proactively) — nudge it once, after
        # yielding a beat for the browser's layout to settle. This alone isn't
        # enough on a phone: the address bar/toolbar often only collapses (changing
        # the real viewport height) off the player's first tap/click on the *page
        # itself* (pygbag's own "Ready to start!" gate used to force that tap; the
        # build turns it off with --ume_block 0, so it is now simply the first one)
        # and that collapse can lag the gesture by more than this beat. So the main loop
        # below fires a second, identical nudge off its own first input event, which
        # is the earliest point simulation code can observe that tap having landed.
        asyncio.ensure_future(_kick_web_resize())
    elif args.phone:
        # Resizable like any window; dragging it to another shape is another phone
        # at the same pixel ratio (taller than wide is portrait).
        pygame.display.set_mode(fake_phone(args.phone)[0], pygame.RESIZABLE)
    else:
        # Resizable: pygame grows the surface with the window, so we never re-call
        # set_mode (a redundant call fights the WM on X11 and snaps it back).
        pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("Star Conquest")
    # Scale the whole UI to the real surface before the first frame: fit the
    # baseline design size into the actual screen. Touch devices (Android, and
    # touch browsers) additionally boost hit targets to stay finger-sized; the web
    # framebuffer is now high-res (WEB_FB_*), so `fit` already scales the UI up for
    # crispness and only *touch* browsers need the extra boost — desktop browsers
    # report no touch points and stay compact. Fonts are built lazily from these
    # sizes, so this must run before any draw (it runs again whenever the window
    # or the player's UI size changes; see the reflow in the loop). The same probe
    # also tells the shell to drop keyboard-only labels/hints (config.touch_ui).
    touch = paths.is_android() or softkeyboard.is_touch_web() or bool(args.phone)
    clock = pygame.time.Clock()

    # Three scenes share the one window: the setup menu, the map creator and the
    # game board. The menu builds `state`/`ui` on "start"; pressing M in-game
    # drops back to it, and Create/Edit map opens the creator between the two.
    menu_state = MenuState()
    boot_size = pygame.display.get_surface().get_size()
    config.apply_ui_scale(ui_scale_for(boot_size, touch, menu_state.ui_size, web_css_px),
                          touch=touch, size=boot_size)
    editor: mapmaker.Editor | None = None      # live only while scene == "maker"
    state: GameState | None = None
    ui: Ui | None = None
    log: GameLog | None = None       # replay log of the live match (None while in menu)
    current_seed = 0
    scene = "game" if args.no_menu else "menu"
    if args.no_menu:
        current_seed = resolve_seed(settings)
        state, ui, log = start_game(settings, current_seed, settings.autoplay)

    # A `#log=<id>` link (or --watch) asks to watch a posted replay. The download
    # runs alongside the menu and is polled once a frame: the browser loop yields
    # per frame, so waiting on a round trip here would freeze the canvas before
    # anything had been drawn.
    wanted = args.watch.strip() or replay_request()
    if wanted and is_log_path(wanted):
        try:
            local = read_game_log(wanted)
        except ValueError as exc:
            ap.error(str(exc))
        if not local.turn_count:
            ap.error(f"{wanted} has no turns to watch")
        pending_replay = share.Download.ready(local.encoded())
    else:
        pending_replay = share.fetch_log(wanted) if wanted else None
    if wanted and pending_replay is None:
        print(f"cannot fetch replay {wanted!r}: no endpoint, or not a match id")
        menu.set_status(menu_state, WATCH_UNREACHABLE_MSG, False)
    elif pending_replay is not None:
        menu.set_status(menu_state, "Loading replay...", True)

    # A `#pbp=<match>:<token>` link (or --match) seats us in a shared match, and a
    # `#spectate=<match>` link (or --spectate) watches one from no seat. Same shape
    # as the replay fetch above — started here, polled once a frame, never awaited
    # — and mutually exclusive with it, since a launch URL carries one fragment.
    pbp_client = PbpClient()
    invite = (pbp.parse_link(pbp.PBP_FRAGMENT + args.match.strip())
              if args.match.strip() else pbp_request())
    watching = (pbp.parse_spectate_link(pbp.SPECTATE_FRAGMENT + args.spectate.strip())
                if args.spectate.strip() else pbp_spectate_request())
    if pending_replay is None:
        line = pbp_client.open_link(invite, watching if invite is None else None)
        if line is not None:
            menu.set_status(menu_state, *line)
    if args.match.strip() and invite is None:
        print(f"--match {args.match.strip()!r} is not a seat link")
        menu.set_status(menu_state, PBP_REFUSED_MSG, False)
    elif args.spectate.strip() and watching is None:
        print(f"--spectate {args.spectate.strip()!r} is not a match id")
        menu.set_status(menu_state, PBP_MISSING_MSG, False)

    # A `#g=<id>` or `#gauntlet=` link opens a gauntlet on the menu's Tutorial
    # list. One fragment per launch, so it never meets the two above.
    pending_gauntlet: pbp.Request | None = None
    linked_gauntlet = False
    if not args.no_menu and pending_replay is None and pbp_client.invite is None:
        gid, inline = gauntlet_request()
        linked_gauntlet = gid is not None or inline is not None
        pending_gauntlet = open_linked_gauntlet(menu_state, settings, gid, inline)

    # A `#daily` or `#puzzle` link fetches a day's daily onto the menu. One
    # fragment per launch, so it never meets the links above.
    pending_daily: pbp.Request | None = None
    pending_daily_kind = daily.BOARD
    # A quick post in flight: the Ui it reports to, the request, and the posted
    # (turns, lost). Landed on that Ui even if the player has moved on.
    pending_post: tuple[Ui, pbp.Request, tuple[int, int]] | None = None
    # The signed-in name a decided game's quick post asked for, and its Ui.
    pending_name: tuple[Ui, pbp.Request] | None = None
    # A won puzzle's par check: the Ui it reports to, the request, and the result asked about.
    pending_par: tuple[Ui, pbp.Request, tuple[int, int]] | None = None
    if not args.no_menu and pending_replay is None and pbp_client.invite is None and not linked_gauntlet:
        asked = daily.link(webstore.url_token())
        if asked is not None:
            linked_gauntlet = True   # a link was opened: no generator, no resume prompt
            pending_daily_kind, asked_day = asked
            pending_daily = open_daily(menu_state, asked_day, pending_daily_kind)

    gen_job: gauntletgen.Job | None = None
    pending_listing: tuple[str, str, pbp.Request] | None = None   # (id, name, save) once a run ends
    if not args.no_menu and pending_replay is None and pbp_client.invite is None and not linked_gauntlet:
        gen_job = start_generation(menu_state, spec_request(args.gauntlet_spec))
        linked_gauntlet = gen_job is not None

    # If the last saved match was left unfinished, offer to resume it on the menu.
    resume_prompt: GameLog | None = None
    if not args.no_menu and pending_replay is None and pbp_client.invite is None and not linked_gauntlet:
        candidate = replay.latest_log()
        if candidate is not None and not candidate.finished and candidate.turn_count > 0:
            resume_prompt = candidate

    running = True
    confirm_quit = False   # showing the "are you sure?" modal; gates every quit path
    confirm_rewind = False  # showing the destructive mid-game rewind confirm modal
    confirm_hint = False    # asking before the first hint, which takes the game off the board
    # History-review snapshots, built on entering history mode and dropped on exit;
    # `live_fog` stashes the live fog triple so it survives a history visit intact.
    reel: turnfilm.Reel | None = None   # the turn being played back, if any
    history_states: list[GameState] = []
    history_fog: list[tuple[set[int], set[int], dict[int, tuple[int, int, float]]]] = []
    history_events: list[list[turnfilm.Event]] = []   # index-aligned with the above
    live_fog: tuple[set[int], set[int], dict[int, tuple[int, int, float]]] | None = None
    auto_accum = 0
    play_accum = 0
    fullscreen = False
    windowed_size = (config.SCREEN_W, config.SCREEN_H)  # restored when leaving fullscreen
    # Web only: re-armed below to fire the second resize kick off the loop's first
    # real input event; already "spent" (never fires) off the web build, so nothing
    # here needs its own is_web() check.
    resize_kicked = not paths.is_web()
    viewport_accum = 0
    while running:
        # Capped, not raw: the frame that resolves a turn can run far longer than
        # a frame, and handing that whole stretch to the film it just started
        # would teleport the glide rather than advance it (`config.MAX_FRAME_MS`).
        dt = min(clock.tick(config.FPS), config.MAX_FRAME_MS)
        pbp_client.idle += dt
        if pending_listing is not None:
            lstate, _lbody = pending_listing[2].poll()
            if lstate != pbp.PENDING:
                land_listing(menu_state, pending_listing[0], pending_listing[1], lstate)
                pending_listing = None
        if pending_gauntlet is not None:
            gstate, gbody = pending_gauntlet.poll()
            if gstate != pbp.PENDING:
                pending_gauntlet = None
                land_gauntlet(menu_state, settings, gstate, gbody)
        if pending_daily is not None:
            dstate, dbody = pending_daily.poll()
            if dstate != pbp.PENDING:
                pending_daily = None
                land_daily(menu_state, settings, dstate, dbody, pending_daily_kind)
        if ui is not None and ui.post_name_due:
            ui.post_name_due = False
            asked = share.signed_in_name()
            pending_name = None if asked is None else (ui, asked)
        if pending_name is not None:
            nstate, nbody = pending_name[1].poll()
            if nstate != pbp.PENDING:
                land_post_name(pending_name[0], nstate, nbody)
                pending_name = None
        if ui is not None and ui.par_due is not None:
            figures, ui.par_due = ui.par_due, None
            asked = daily.par_check(ui.daily_day, *figures)
            pending_par = None if asked is None else (ui, asked, figures)
        if pending_par is not None:
            rstate, rbody = pending_par[1].poll()
            if rstate != pbp.PENDING:
                land_par(pending_par[0], pending_par[2], rstate, rbody)
                pending_par = None
        if pending_post is not None:
            pstate, pbody = pending_post[1].poll()
            if pstate != pbp.PENDING:
                land_quick_post(pending_post[0], pending_post[2], pstate, pbody)
                pending_post = None
        if pending_replay is not None:
            status, body = pending_replay.poll()
            if status != share.PENDING:
                opened = open_replay(body, settings) if status == share.OK else None
                pending_replay = None
                if status != share.OK:
                    # `missing` is the board answering "no such replay" — the
                    # plumbing worked, so the player is told to look at the score,
                    # not at their connection. Anything else is no answer at all:
                    # offline, CORS, an unconfigured or undeployed endpoint. The
                    # console carries the URL that was actually asked.
                    print(f"replay fetch ended in state {status!r}")
                    menu.set_status(menu_state,
                                    WATCH_MISSING_MSG if status == share.MISSING
                                    else WATCH_UNREACHABLE_MSG, False)
                elif opened is None:
                    # It answered, and what came back either isn't a replay at all
                    # (an error page, a truncated body, a log with no turns in it)
                    # or is one recorded under rules the engine has since moved
                    # past — decoded again here only to tell the two apart for the
                    # status line; `open_replay` already made the same call.
                    stale = _decode_log(body)
                    if stale is not None and not stale.is_current:
                        print(f"replay was recorded under rules v{stale.rules_version}, "
                              f"this build plays v{engine.RULES_VERSION}")
                        menu.set_status(menu_state, WATCH_OUTDATED_MSG, False)
                    else:
                        print(f"replay body was not a readable log ({len(body)} bytes): "
                              f"{body[:120]!r}")
                        menu.set_status(menu_state, WATCH_UNREADABLE_MSG, False)
                else:
                    state, ui, log = opened
                    current_seed = log.seed
                    history_states, history_fog, history_events, live_fog = open_history(state, ui, log)
                    # Nothing to review means nothing to watch: fall back to the
                    # menu rather than dropping into a board with no history.
                    scene = "game" if history_states else "menu"
        # Play-by-post, all three calls in one place (`pbpclient`).
        heard = pbp_client.step(dt, state, ui, log, settings, filming=reel is not None)
        if heard.status is not None:
            menu.set_status(menu_state, *heard.status)
        if heard.created:
            resume_prompt = None
        if heard.game is not None:
            state, ui, log = heard.game
        if heard.opened:
            current_seed = heard.seed
            scene = "game"
            auto_accum = 0
        if heard.rebuilt:
            history_states, history_fog, history_events = [], [], []
            live_fog = None
        if heard.reel is not None:
            reel = heard.reel
        if heard.played:
            play_accum = auto_accum = 0

        # Web: the surface only changes size when we resize it, so follow the page
        # (not while F11's fullscreen mode owns it).
        if paths.is_web() and not fullscreen:
            viewport_accum += dt
            if viewport_accum >= WEB_VIEWPORT_POLL_MS:
                viewport_accum = 0
                fit_web_canvas()
        # Reflow to fill the window whenever its size, or the UI size, changes.
        screen = pygame.display.get_surface()
        scale = ui_scale_for(screen.get_size(), touch, menu_state.ui_size, web_css_px)
        if screen.get_size() != (config.SCREEN_W, config.SCREEN_H) or scale != config.ui_scale:
            config.SCREEN_W, config.SCREEN_H = screen.get_size()
            config.apply_ui_scale(scale, touch=touch)
            if state is not None and ui is not None:
                ui.view = build_view(state)
                ui.reset_view(state)   # re-frame for the new size, not the whole map
            if editor is not None:
                mapmaker.reflow(editor)
        for event in pygame.event.get():
            if event.type in PBP_ACTIVITY_EVENTS:
                pbp_client.idle = 0
            # Web only, once: the first tap/click/key this session sees is the
            # earliest point simulation code can observe that the player has
            # actually landed on the page (touch arrives as MOUSEBUTTONDOWN — see
            # the boot-time kick's comment for why this second nudge matters, and
            # why a fixed delay from boot alone isn't a substitute for it).
            if not resize_kicked and event.type in (
                    pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                resize_kicked = True
                asyncio.ensure_future(_kick_web_resize())
            # Android hardware/gesture Back arrives as K_AC_BACK; normalise it to
            # Esc so every existing "cancel / back out" handler below just works.
            if event.type == pygame.KEYDOWN and event.key == pygame.K_AC_BACK:
                event = pygame.event.Event(
                    pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="\x1b", mod=0)
            if confirm_quit:
                # Modal: swallow all other input until the user answers.
                do_quit = False
                if event.type == pygame.QUIT:
                    running = False       # the OS/WM is closing us; not our choice
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        do_quit = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        confirm_quit = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    quit_r, cancel_r = render.confirm_quit_buttons(screen)
                    if quit_r.collidepoint(event.pos):
                        do_quit = True
                    elif cancel_r.collidepoint(event.pos):
                        confirm_quit = False
                if do_quit:
                    if leave_app():
                        running = False
                    else:
                        # web: the browser wouldn't close the window, so fall back to
                        # the setup menu and say so — never to a dead black canvas.
                        confirm_quit = False
                        scene = "menu"
                        state, ui, log = None, None, None
                        menu.set_status(menu_state, CANT_CLOSE_MSG, False)
                continue

            if confirm_rewind:
                # Modal: confirm a destructive mid-game rewind (discards later turns).
                do_rewind = False
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        do_rewind = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        confirm_rewind = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    rewind_r, cancel_r = render.confirm_rewind_buttons(screen)
                    if rewind_r.collidepoint(event.pos):
                        do_rewind = True
                    elif cancel_r.collidepoint(event.pos):
                        confirm_rewind = False
                if do_rewind:
                    assert log is not None and ui is not None   # confirm_rewind ⇒ in-game
                    stamped = ui.campaign_start   # the same match, so the same start
                    state, ui = apply_rewind(log, settings, ui.history_turn)
                    ui.campaign_start = stamped
                    stage_ui(ui, menu.active_stage(menu_state, settings), briefing=False)
                    history_states, history_fog, history_events, live_fog = [], [], [], None
                    confirm_rewind = False
                    auto_accum = 0
                continue

            if confirm_hint:
                # Modal: what a hint is, and that the game can't be posted after one.
                do_hint = False
                if event.type == pygame.QUIT:
                    confirm_hint, confirm_quit = False, True
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        do_hint = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        confirm_hint = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    show_r, cancel_r = render.confirm_hint_buttons(screen)
                    if show_r.collidepoint(event.pos):
                        do_hint = True
                    elif cancel_r.collidepoint(event.pos):
                        confirm_hint = False
                if do_hint:
                    assert state is not None and ui is not None   # confirm_hint ⇒ in-game
                    take_hint(state, ui, log)
                    confirm_hint = False
                continue

            if resume_prompt is not None:
                # Boot modal: resume the last unfinished game, or dismiss to the menu.
                accept = False
                if event.type == pygame.QUIT:
                    confirm_quit = True
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        accept = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        resume_prompt = None
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    resume_r, new_r = menu.resume_prompt_buttons(screen)
                    if resume_r.collidepoint(event.pos):
                        accept = True
                    elif new_r.collidepoint(event.pos):
                        resume_prompt = None
                if accept:
                    assert resume_prompt is not None
                    # A run's own game comes back as the run's, so it keeps the
                    # run's rules and its result reaches the run.
                    if gauntlet_run.is_playing(resume_prompt):
                        resumed_game = open_run(menu_state, settings, fresh=False)
                    else:
                        ai.load_models()   # a resumed game may name a drop-in strategy
                        resumed = resume_game(resume_prompt, settings)
                        resumed_game = (*resumed, resume_prompt, resume_prompt.seed)
                    if resumed_game is not None:
                        state, ui, log, current_seed = resumed_game
                        scene = "game"
                        auto_accum = 0
                    resume_prompt = None
                continue

            if event.type == pygame.QUIT:
                confirm_quit = True
                continue

            if (event.type == pygame.KEYDOWN and event.key == pygame.K_F11
                    and not paths.is_android()):
                # Desktop-only: mobile is already fullscreen and re-calling
                # set_mode mid-run is fragile on Android's SDL.
                fullscreen = not fullscreen
                if fullscreen:
                    windowed_size = (config.SCREEN_W, config.SCREEN_H)
                    pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                else:
                    pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
                continue

            if event.type == pygame.KEYDOWN and not touch:
                pct = ui_size_key(event, menu_state.ui_size)
                if pct is not None:
                    menu.set_ui_size(menu_state, pct)
                    continue

            if scene == "menu":
                action = menu.handle_event(event, menu_state, settings)
                if action == "start":
                    state, ui, log, current_seed = _begin_game(
                        settings, menu.active_stage(menu_state, settings))
                    stamp_campaign(ui, menu_state.campaign_watch)
                    scene = "game"
                    auto_accum = 0
                elif action in ("run", "run_continue"):
                    run_game = open_run(menu_state, settings, fresh=action == "run")
                    if run_game is not None:
                        state, ui, log, current_seed = run_game
                        stamp_campaign(ui, menu_state.campaign_watch)
                        scene = "game"
                        auto_accum = 0
                    else:
                        menu.reopen_gauntlets(menu_state, settings)
                elif action in ("daily", "puzzle"):
                    if pending_daily is None:
                        pending_daily_kind = daily.PUZZLE if action == "puzzle" else daily.BOARD
                        pending_daily = open_daily(menu_state, kind=pending_daily_kind)
                elif action == "play_by_post":
                    # The same setup, opened as a shared match — for the roster
                    # and deadline the menu's own prompt just confirmed (seat 1
                    # is always in the roster; a seat left off plays whatever
                    # strategy it already carries). The seed is resolved now and
                    # sent with it: "roll a fresh seed" has to mean one seed for
                    # the whole table, not one each.
                    if pbp_client.make is None:
                        current_seed = resolve_seed(settings)
                        line = pbp_client.create(
                            settings, current_seed, sorted(menu_state.pbp_roster) or None,
                            menu_state.pbp_deadline_hours, menu_state.pbp_public,
                            menu_state.pbp_title, menu_state.pbp_name)
                        if line is not None:
                            menu.set_status(menu_state, *line)
                elif action == "create_map":
                    editor = mapmaker.open_editor(settings)
                    scene = "maker"
                elif action == "quit":
                    confirm_quit = True
                continue

            if scene == "maker":
                assert editor is not None
                action = mapmaker.handle_event(event, editor, settings)
                if action == "play":
                    state, ui, log, current_seed = _begin_game(settings)
                    scene, editor = "game", None
                elif action == "menu":
                    scene, editor = "menu", None
                continue

            # Past the menu and modal handlers, so scene == "game": state/ui/log are live.
            assert state is not None and ui is not None and log is not None
            action = game_input.handle_event(event, state, ui)
            if action == "hint":
                confirm_hint = press_hint(state, ui, log)
            elif action == "quit":
                confirm_quit = True
            elif action == "menu":
                scene = "menu"
                state, ui, log = None, None, None
            elif action == "pbp_copy_seat":
                # The invite overlay named the seat (`input` never touches the
                # clipboard); the link is whichever row it was drawn from.
                link = next((link for seat, link in ui.pbp_invite
                            if seat == ui.pbp_copy_seat), "")
                if link and webstore.copy_to_clipboard(link):
                    ui.pbp_invite_copied = ui.pbp_copy_seat
                ui.pbp_copy_seat = 0
            elif action == "share":
                # Game-over only (input only returns this there), and only for a
                # result that is ours to publish — render gates the button on the
                # very same call, so the C key cannot reach what the overlay
                # declines to draw.
                if ui.can_post(state):
                    ui.share_msg = (copy_daily_result(settings, state, ui) if ui.daily_day
                                    else share_challenge(settings, state, ui, current_seed, log))
            elif action == "leaderboard":
                # Same gate as "share": the two buttons offer one result by two
                # channels, so neither may fire on a result that isn't yours.
                if ui.can_post(state):
                    board = ui.board_action()
                    if board == "view":
                        ui.share_msg = open_map_page(ui.posted_key) or ui.share_msg
                    elif board == "post":
                        request = quick_post(settings, state, ui, current_seed, log)
                        if request is not None:
                            figures = (state.turn, state.players[ui.human_id].ships_lost)
                            pending_post = (ui, request, figures)
                    elif board == "enter":
                        ui.share_msg = post_to_leaderboard(settings, state, ui, current_seed, log)
            elif action == "end_turn" and not ui.autoplay:
                if ui.in_pbp:
                    # The one thing that makes a shared match different: the turn
                    # advances when the last seat submits, not when anybody
                    # presses a key. So this sends ours and waits (`input` already
                    # declines a second press while we are waiting).
                    pbp_client.send(state, ui)
                else:
                    reel = resolve_turn(state, ui, log, settings)
                play_accum = 0   # re-time the play cadence from this step
            elif action == "toggle_play":
                apply_toggle_play(ui)
                play_accum = 0   # first step after PLAY_MS, then every PLAY_MS
            elif action == "toggle_autoplay":
                ui.autoplay = not ui.autoplay
                ui.playing = False
                ui.reset_selection()
                ui.reset_route()
                ui.clear_pending()
                auto_accum = 0
            elif action == "toggle_fast_forward":
                toggle_fast_forward(state, ui)   # spectating only; else a no-op
                auto_accum = play_accum = 0
            elif action == "restart" and not ui.in_stage:
                current_seed += 1
                state, ui, log = start_game(settings, current_seed,
                                            carry_autoplay(ui))
            elif action == "next_stage" and ui.offers_next_stage(state):
                # The stage after this one, briefing and all. The menu's setup
                # becomes that stage's, so going back to the menu lands on it.
                # In a run it is the run's next stage, begun as its next step.
                if ui.run_id:
                    next_game = open_run(menu_state, settings, fresh=False)
                else:
                    found = menu.next_stage(menu_state, settings)
                    next_game = _begin_game(settings, found) if found is not None else None
                if next_game is not None:
                    state, ui, log, current_seed = next_game
                    stamp_campaign(ui, menu_state.campaign_watch)
                    history_states, history_fog, history_events, live_fog = [], [], [], None
                    auto_accum = 0
            elif action == "retry":
                # Replay this exact match from the opening position: fork the
                # log at turn 0 so the completed record stays intact, then
                # resume live play in the new file (same recipe as rewinding
                # to turn 0 from history, minus the scrubbing).
                assert log is not None
                log = log.fork(0)
                try:
                    log.save()
                except OSError:
                    pass
                current_seed = log.seed
                was_watched = ui.watched
                state, ui = resume_game(log, settings)
                if not was_watched:
                    stamp_campaign(ui, menu_state.campaign_watch)
                    stage_ui(ui, menu.active_stage(menu_state, settings), briefing=False)
                history_states, history_fog, history_events, live_fog = [], [], [], None
                auto_accum = 0
            elif action == "undo" and ui.can_undo(state):
                # Rewind the live game one turn, as History's "Rewind to here" on
                # the turn before does, without the scrubbing or the confirm.
                assert log is not None
                stamped = ui.campaign_start   # the same match, so the same start
                state, ui = apply_rewind(log, settings, max(log.turn_count - 1, 0))
                ui.campaign_start = stamped
                stage_ui(ui, menu.active_stage(menu_state, settings), briefing=False)
                history_states, history_fog, history_events, live_fog = [], [], [], None
                auto_accum = 0
            elif action == "toggle_route":
                # Route mode: multi-select a group of systems and forward them all
                # toward one destination. Leaving discards the plan — it is only
                # ever committed by its own confirm.
                if ui.mode == viewstate.ROUTING:
                    ui.reset_route()
                elif ui.can_route(state):
                    ui.begin_route()
            elif action == "toggle_history":
                if ui.history:
                    # leave review: drop snapshots and restore the live fog exactly
                    # (the live board is untouched while reviewing, so no recompute).
                    ui.history = False
                    ui.dragging_scrubber = False
                    ui.dragging_graph = False
                    ui.history_chart = None
                    ui.coach_review, ui.coach_open, ui.coach_sel = None, False, -1
                    ui.history_graph = ui.graph_full = False
                    ui.playing = False   # replay playback must not carry into live play
                    ui.clear_fading_marks()   # they belonged to a review that just ended
                    ui.view.settle(config.LIVE_ROOM)   # back inside live play's (tighter) room
                    history_states, history_fog, history_events = [], [], []
                    if live_fog is not None:
                        ui.visible, ui.seen, ui.player_intel = live_fog
                        live_fog = None
                    ui.leave_history_rules()
                elif log is not None and log.turn_count > 0 and ui.can_review(state):
                    ui.clear_fading_marks()   # entering fresh: nothing from live play carries in
                    history_states, history_fog, history_events, entered = open_history(state, ui, log)
                    if entered is not None:
                        live_fog = entered
            elif action == "rewind" and not ui.in_pbp:
                # Never reachable from a shared match — render withholds the button
                # (see `_draw_scrubber`) — but the check is repeated here rather
                # than trusted, since rewinding forks the view away from a match
                # the server and every other seat still think is at a later turn.
                if ui.history_reveal:
                    # finished game — fork a new save so the completed record stays
                    # intact, then resume live play in the new file.
                    log = log.fork(ui.history_turn)
                    try:
                        log.save()
                    except OSError:
                        pass
                    current_seed = log.seed
                    was_watched = ui.watched
                    state, ui = resume_game(log, settings)
                    if not was_watched:
                        stamp_campaign(ui, menu_state.campaign_watch)
                        stage_ui(ui, menu.active_stage(menu_state, settings), briefing=False)
                    history_states, history_fog, history_events, live_fog = [], [], [], None
                    auto_accum = 0
                else:
                    confirm_rewind = True   # mid-game rewind is destructive: confirm

        if scene == "game":
            assert state is not None and ui is not None and log is not None
            campaign_tick(ui, settings, menu_state.campaign_watch)
            # A mark keeps fading whether or not a playback is currently running
            # (that's the whole point — see `Ui.age_fading_marks`), so this runs
            # unconditionally, every frame, regardless of what `reel` is doing.
            ui.age_fading_marks(dt)
            # A playback holds the board for its duration, and nothing below may
            # resolve or seek while it runs — under play mode the next turn would
            # otherwise be resolved out from under the one still being drawn.
            # `ui.film` and `reel` are kept in lockstep, so the skip that clears
            # the film (in `input`) also drops the board it was playing onto.
            if reel is not None and ui.film is None:
                reel = None                 # skipped: input cleared the film
                land_film(state, ui)        # ...which still leaves the snap owed
            elif reel is None and ui.film is not None:
                land_film(state, ui)        # unreachable, but a stranded film would
                                            # hide the End Turn button for good
            elif reel is not None and not confirm_quit and not confirm_rewind and not confirm_hint:
                # Frozen rather than advanced while paused (see `Ui.film_paused`),
                # so pausing keeps showing what the turn did instead of losing it.
                if not ui.film_paused:
                    ui.film_ms += dt
                    ui.archive_marks(reel.board, reel.run_to(ui.film_ms))
                if ui.film_ms >= ui.film.total_ms:
                    # By how much this frame's step overran the film. A run of
                    # turns is one continuous glide (see the chaining below), so
                    # it belongs to the turn that follows rather than on the
                    # floor — `_carry_into` pays it there.
                    carry = ui.film_ms - ui.film.total_ms
                    if ui.history:
                        # The scrubber moves at the film's *end*, so it and the top
                        # bar's turn counter (which reads the board being drawn)
                        # never disagree mid-transition.
                        ui.history_turn = min(ui.history_max, ui.history_turn + 1)
                        if ui.history_turn >= ui.history_max:
                            ui.playing = False
                    land_film(state, ui)
                    reel = None
                    play_accum = 0
                    auto_accum = 0
                    if ui.history and ui.playing:
                        # Chain straight into the next turn's film with no gap, so
                        # a run of animated turns glides rather than stuttering —
                        # the PLAY_MS pacing below is only for a quiet turn with
                        # nothing to chain into.
                        reel = _next_history_film(ui, history_states, history_fog,
                                                  history_events)
                    elif (ui.playing and not ui.history and state.winner is None
                            and ui.mode != viewstate.ROUTING):
                        # ...and live play chains the same way, on the same terms
                        # as the PLAY_MS branch below it would have resolved on.
                        reel = resolve_turn(state, ui, log, settings)
                    elif (ui.autoplay and not ui.history and state.winner is None
                            and ui.mode != viewstate.ROUTING):
                        # ...and so does autoplay, on the same terms as the
                        # AUTOPLAY_MS branch below it would have resolved on — a bot
                        # game gets the same continuous glide a human's does.
                        reel = resolve_turn(state, ui, log, settings)
                    if reel is not None:
                        _carry_into(ui, reel, carry)

            if (reel is None and ui.history and ui.playing
                    and not confirm_quit and not confirm_rewind and not confirm_hint):
                # Replay playback: one turn per PLAY_MS, stopping when it reaches
                # the final recorded turn (like a video reaching the end). With
                # turn animation on, the step becomes a film and the scrubber
                # advances when that finishes instead of here — and the turn after
                # that one chains immediately (just above), so this pacing is only
                # ever felt on a turn with nothing to animate.
                play_accum += dt
                if play_accum >= PLAY_MS:
                    play_accum = 0
                    reel = _next_history_film(ui, history_states, history_fog, history_events)
                    if reel is None:
                        nxt = ui.history_turn + 1
                        ui.history_turn = min(ui.history_max, nxt)
                        if ui.history_turn >= ui.history_max:
                            ui.playing = False
            elif (reel is None and state.winner is None
                    and not confirm_quit and not confirm_rewind and not confirm_hint and not ui.history
                    and ui.mode != viewstate.ROUTING):
                if ui.autoplay:
                    # Same shape as play mode just below: with turn animation on,
                    # one animated turn chains into the next as it lands (above),
                    # so this pacing is only ever felt on a turn with nothing to
                    # animate (or with the preference off).
                    auto_accum += dt
                    if auto_accum >= step_delay(ui, AUTOPLAY_MS):
                        auto_accum = 0
                        reel = resolve_turn(state, ui, log, settings)
                elif ui.playing:
                    # Same shape as history's playback above: with turn animation
                    # on, one animated turn chains into the next as it lands, so
                    # this pacing is only ever felt on a turn with nothing to
                    # animate (or with the preference off).
                    play_accum += dt
                    if play_accum >= step_delay(ui, PLAY_MS):
                        play_accum = 0
                        reel = resolve_turn(state, ui, log, settings)

        if scene == "menu":
            # Soft-keyboard typing arrives outside the SDL event queue, so the
            # menu needs a per-frame poll as well as its event handler.
            menu.pump(menu_state, settings)
            if gen_job is not None:
                ran = gen_job
                gen_job = pump_generation(menu_state, settings, gen_job)
                if gen_job is None:
                    pending_listing = ask_listing(menu_state, ran)
            menu.draw(screen, menu_state, settings)
            if resume_prompt is not None:
                menu.draw_resume_prompt(screen, resume_prompt)
        elif scene == "maker":
            assert editor is not None
            mapmaker.pump(editor)          # same soft-keyboard poll, for its filename field
            mapmaker.age_status(editor, dt)
            mapmaker.draw(screen, editor, settings)
        else:
            assert state is not None and ui is not None   # scene == "game"
            if ui.history and history_states:
                # Draw the reconstructed past board with the fog for that turn — or, for
                # a finished game, everything revealed. `render` just draws the state and
                # fog it's handed; it neither knows nor cares the board is historical.
                #
                # Mid-transition it is handed the film's board instead, and the fog of
                # the turn being moved *to*: a film is a report on a turn that has
                # already happened, and holding the earlier fog would have an inbound
                # fleet pop into existence halfway down its lane.
                i = max(0, min(ui.history_turn, len(history_states) - 1))
                view_state = history_states[i]
                if reel is not None:
                    view_state = reel.board
                    i = min(i + 1, len(history_fog) - 1)
                if ui.history_reveal:
                    all_ids = set(view_state.systems)
                    ui.visible, ui.seen, ui.player_intel = all_ids, set(all_ids), {}
                else:
                    ui.visible, ui.seen, ui.player_intel = history_fog[i]
                ui.show_history_rules()
                if reel is None:
                    hold_history_marks(ui, history_states, history_fog, history_events)
                render.draw(screen, view_state, ui)
            else:
                # Live play, and the same substitution: `resolve_turn` has already
                # refreshed the fog, so a film runs under the fog of the board it is
                # about to hand back — the same choice as above.
                render.draw(screen, reel.board if reel is not None else state, ui)
            if confirm_rewind:   # only ever set in-game, so ui is live here
                render.draw_confirm_rewind(screen, ui.history_turn)
            if confirm_hint:
                render.draw_confirm_hint(screen, ui, HINT_STRATEGY.title())
        if confirm_quit:
            render.draw_confirm_quit(screen)
        pygame.display.flip()
        # Yield to the browser event loop each frame (required by pygbag/Emscripten;
        # a harmless near-instant await on desktop).
        await asyncio.sleep(0)

    pygame.quit()


def _crash_screen(exc_text: str) -> None:
    """Last-resort on-device error display. A phone has no console or logcat to
    hand, so on an unhandled exception we draw the traceback to the screen (and
    save it to the data dir) so it can be read or screenshotted. Best-effort: if
    the display itself is what failed, this just returns."""
    try:
        (paths.data_dir() / "last_crash.txt").write_text(exc_text)
    except Exception:
        pass
    try:
        if not pygame.get_init():
            pygame.init()
        screen = pygame.display.get_surface() or pygame.display.set_mode((0, 0))
        w, h = screen.get_size()
        font = pygame.font.SysFont("monospace", max(14, w // 60))
        char_w = max(1, font.size("M")[0])
        margin, line_h = 16, font.get_height() + 2
        cols = max(20, (w - 2 * margin) // char_w)
        lines: list[str] = []
        for raw in exc_text.splitlines():
            lines.extend([raw[i:i + cols] for i in range(0, len(raw), cols)] or [""])
        screen.fill((12, 12, 18))
        for row, ln in enumerate(lines[: (h - 2 * margin) // line_h]):
            screen.blit(font.render(ln, True, (240, 130, 130)),
                        (margin, margin + row * line_h))
        pygame.display.flip()
        clock = pygame.time.Clock()
        waiting = True
        while waiting:
            for ev in pygame.event.get():
                if ev.type in (pygame.QUIT, pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                    waiting = False
            clock.tick(30)
    except Exception:
        pass


def run() -> None:
    """Run the game to exit; what the root ``main.py`` calls."""
    # pygbag patches asyncio.run to drive the browser event loop; on desktop this
    # is the ordinary asyncio entry point. (In the browser, code after this line
    # may run before the game finishes, so all shutdown lives inside main().)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pygame.quit()
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise                 # a clean exit or a usage error is not a crash
        import traceback
        traceback.print_exc()     # -> terminal on desktop, browser console on web
        if not paths.is_web():
            _crash_screen(traceback.format_exc())
        pygame.quit()
    print("\nGoodbye!")


if __name__ == "__main__":
    run()
