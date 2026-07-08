"""Star Conquest — entry point and pygame main loop.

    uv run python main.py                 # random map, 3 players, you are blue
    uv run python main.py --mode symmetric --players 4
    uv run python main.py --autoplay --seed 1   # AI plays every seat (a demo)

The loop owns transient view-state and wiring only; all rules live in the pure
core (engine/combat/mapgen/ai), and all drawing lives in render.
"""

from __future__ import annotations

import argparse
import random

import pygame

from starconquest import ai, config, engine, mapgen, render
from starconquest import input as game_input
from starconquest.geometry import WorldView
from starconquest.model import GameState, Order
from starconquest.viewstate import Ui

AUTOPLAY_MS = 350  # delay between auto-resolved turns in autoplay mode


def build_view(state: GameState) -> WorldView:
    return WorldView(mapgen.map_bounds(state), config.play_rect(), padding=50)


def new_ui(state: GameState, autoplay: bool) -> Ui:
    return Ui(view=build_view(state), human_id=1, autoplay=autoplay)


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
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)

    def make_state(s: int) -> GameState:
        return mapgen.generate(s, args.mode, args.nodes, args.players)

    pygame.init()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    pygame.display.set_caption("Star Conquest")
    clock = pygame.time.Clock()

    state = make_state(seed)
    ui = new_ui(state, args.autoplay)

    running = True
    auto_accum = 0
    while running:
        dt = clock.tick(config.FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break
            action = game_input.handle_event(event, state, ui)
            if action == "quit":
                running = False
            elif action == "end_turn" and not ui.autoplay:
                resolve_turn(state, ui)
            elif action == "toggle_autoplay":
                ui.autoplay = not ui.autoplay
                ui.reset_selection()
                ui.clear_pending()
                auto_accum = 0
            elif action == "restart":
                seed += 1
                state = make_state(seed)
                ui = new_ui(state, ui.autoplay)

        if ui.autoplay and state.winner is None:
            auto_accum += dt
            if auto_accum >= AUTOPLAY_MS:
                auto_accum = 0
                resolve_turn(state, ui)

        render.draw(screen, state, ui)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
