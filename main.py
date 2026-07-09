"""Star Conquest — entry point and pygame main loop.

    uv run python main.py                       # opens the setup menu
    uv run python main.py --players 4           # menu pre-filled from CLI args
    uv run python main.py --no-menu --autoplay  # skip the menu (scriptable demo)

CLI args pre-fill the setup menu; --no-menu starts a game straight from them.
The loop owns transient view-state and scene wiring only; all rules live in the
pure core (engine/combat/mapgen/ai), and all drawing lives in render/menu.
"""

from __future__ import annotations

import argparse

import pygame

from starconquest import ai, config, engine, mapgen, menu, render
from starconquest import input as game_input
from starconquest.geometry import WorldView
from starconquest.menu import MenuState
from starconquest.model import GameState, Order
from starconquest.settings import Settings, build_state, resolve_seed
from starconquest.viewstate import Ui

AUTOPLAY_MS = 350  # delay between auto-resolved turns in autoplay mode


def build_view(state: GameState) -> WorldView:
    return WorldView(mapgen.map_bounds(state), config.play_rect(), padding=50)


def new_ui(state: GameState, autoplay: bool) -> Ui:
    return Ui(view=build_view(state), human_id=1, autoplay=autoplay)


def start_game(settings: Settings, seed: int, autoplay: bool) -> tuple[GameState, Ui]:
    state = build_state(settings, seed)
    return state, new_ui(state, autoplay)


def auto_forward_orders(state: GameState, ui: Ui) -> list[Order]:
    """Turn standing auto-forward rules into this turn's orders for the human.

    A rule keeps ``keep`` ships at the source and forwards the surplus onward.
    We subtract ships already promised by manually-queued orders from the same
    source so a rule cooperates with (rather than double-counts) manual sends.
    """
    orders: list[Order] = []
    for src, (dest, keep) in ui.auto_forward.items():
        sys = state.systems.get(src)
        if sys is None or sys.owner_id != ui.human_id:
            continue  # dormant while the system isn't ours (may resume if recaptured)
        if not state.are_adjacent(src, dest):
            continue
        send = ui.available(state, src) - keep
        if send > 0:
            orders.append(Order(ui.human_id, src, dest, send))
    return orders


def resolve_turn(state: GameState, ui: Ui) -> None:
    """Advance one turn. In autoplay the human seat is also driven by the AI."""
    human_orders = (
        ai.compute_orders(state, ui.human_id)
        if ui.autoplay
        else list(ui.pending) + auto_forward_orders(state, ui)
    )
    engine.end_turn(state, human_orders=human_orders, decide=ai.compute_orders)
    ui.clear_pending()
    ui.reset_selection()


def main() -> None:
    ap = argparse.ArgumentParser(description="Star Conquest")
    ap.add_argument("--seed", type=int, default=None, help="map seed (random if omitted)")
    ap.add_argument("--mode", choices=["random", "symmetric"], default="random")
    ap.add_argument("--players", type=int, default=config.DEFAULT_PLAYERS)
    ap.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    ap.add_argument("--autoplay", action="store_true", help="AI plays all seats")
    ap.add_argument("--no-menu", action="store_true",
                    help="skip the setup menu and start straight away with these args")
    args = ap.parse_args()

    settings = Settings.from_args(args)

    pygame.init()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    pygame.display.set_caption("Star Conquest")
    clock = pygame.time.Clock()

    # Two scenes share the one window: the setup menu and the game board. The
    # menu builds `state`/`ui` on "start"; pressing M in-game drops back to it.
    menu_state = MenuState()
    state: GameState | None = None
    ui: Ui | None = None
    current_seed = 0
    scene = "game" if args.no_menu else "menu"
    if args.no_menu:
        current_seed = resolve_seed(settings)
        state, ui = start_game(settings, current_seed, settings.autoplay)

    running = True
    auto_accum = 0
    while running:
        dt = clock.tick(config.FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break

            if scene == "menu":
                action = menu.handle_event(event, menu_state, settings)
                if action == "start":
                    current_seed = resolve_seed(settings)
                    state, ui = start_game(settings, current_seed, settings.autoplay)
                    scene = "game"
                    auto_accum = 0
                elif action == "quit":
                    running = False
                continue

            action = game_input.handle_event(event, state, ui)
            if action == "quit":
                running = False
            elif action == "menu":
                scene = "menu"
                state, ui = None, None
            elif action == "end_turn" and not ui.autoplay:
                resolve_turn(state, ui)
            elif action == "toggle_autoplay":
                ui.autoplay = not ui.autoplay
                ui.reset_selection()
                ui.clear_pending()
                auto_accum = 0
            elif action == "restart":
                current_seed += 1
                state, ui = start_game(settings, current_seed, ui.autoplay)

        if scene == "game" and ui.autoplay and state.winner is None:
            auto_accum += dt
            if auto_accum >= AUTOPLAY_MS:
                auto_accum = 0
                resolve_turn(state, ui)

        if scene == "menu":
            menu.draw(screen, menu_state, settings)
        else:
            render.draw(screen, state, ui)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
