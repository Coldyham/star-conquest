"""Which bot plays each seat, as the HUD names it: the top bar's hover tooltip
and the graph legend, with a random seat kept secret until the game is decided."""




from starconquest import ai, app


def test_a_stop_that_repeats_its_family_is_left_off():
    """"Thinker · Thinker" says nothing a plain "Thinker" doesn't."""
    from starconquest import mapgen
    ai.load_models()
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    names = ai.aux_names("thinker") or ()
    assert "Thinker" in names
    state.players[2].ai_strategy = "thinker"
    spec = ai.aux_spec("thinker")
    assert spec is not None
    state.players[2].ai_params.aux = float(spec[1] + names.index("Thinker"))
    assert app.seat_bot_labels(state)[2] == "Thinker"
