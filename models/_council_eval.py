"""council's judge: board features and a small learned value function.

Underscore-prefixed so `ai.load_models` skips it (it has no `decide`); council
loads it by path.

Pure Python, no numpy: the model ships as constant tables (between the
``WEIGHTS`` markers) and is evaluated by `value`, a few hundred multiply-adds.
It was fitted offline on roster self-play: features of a board every second
turn, labelled with whether that seat went on to win. Every feature is from
``pid``'s point of view and scaled to a board-size-free range, so one model
serves 7 nodes and 40.

Nothing here reads `state.rng` or mutates the board.
"""
from __future__ import annotations

import math

from starconquest import config

# The perspective's opponent: knower's material score picks the strongest rival.
_RIVAL_PROD, _RIVAL_SYS, _RIVAL_SHIPS = 10.0, 1.0, 0.15

FEATURES = (
    "inc_share", "ship_share", "sys_share",
    "inc_gap", "ship_gap", "sys_gap",
    "near_me", "mid_me", "far_me",
    "near_riv", "mid_riv", "far_riv",
    "threat_me", "threat_riv",
    "exposed_me", "exposed_riv",
    "neutral_reach_me", "neutral_reach_riv", "neutral_share",
    "contact", "rivals_alive", "seats", "nodes", "lane_turns", "turn",
    "advantage", "jitter", "pipeline_me", "pipeline_riv",
)


class _Seat:
    __slots__ = ("inc", "gar", "nsys", "near", "mid", "far", "threat", "exposed",
                 "reach", "pipeline", "contact")

    def __init__(self) -> None:
        self.inc = self.gar = self.near = self.mid = self.far = 0.0
        self.threat = self.exposed = self.reach = self.pipeline = 0.0
        self.nsys = 0
        self.contact = False

    def ships(self) -> float:
        return self.gar + self.near + self.mid + self.far

    def material(self) -> float:
        return _RIVAL_PROD * self.inc + _RIVAL_SYS * self.nsys + _RIVAL_SHIPS * self.ships()


def _seats(state) -> dict[int, _Seat]:
    seats = {p.id: _Seat() for p in state.players.values() if not p.is_neutral}
    systems = state.systems
    for s in systems.values():
        q = seats.get(s.owner_id)
        if q is None:
            continue
        q.nsys += 1
        q.gar += s.ships
        if s.production > 0:
            q.inc += 1.0 / s.production
            q.pipeline += s.prod_progress / s.production
        enemy_next = 0
        for n in s.neighbors:
            t = systems[n]
            if t.owner_id == s.owner_id:
                continue
            if t.owner_id == 0:
                if t.production > 0:
                    q.reach += 1.0 / t.production
            else:
                q.contact = True
                enemy_next += t.ships
        q.exposed += max(0, enemy_next - s.ships)
    for f in state.fleets:
        q = seats.get(f.owner_id)
        if q is None:
            continue
        left = f.turns_remaining
        if left <= 1:
            q.near += f.ships
        elif left <= 3:
            q.mid += f.ships
        else:
            q.far += f.ships
        target = seats.get(systems[f.dest_id].owner_id)
        if target is not None and target is not q:
            target.threat += f.ships
    return seats


# knower's TUNED_SWING (1.1 / 0.9). A gentler jitter must not move the plan.
TUNED_JITTER = 0.10

def features(state, pid: int) -> list[float]:
    """The board as ``pid`` should weigh it, in `FEATURES` order."""
    seats = _seats(state)
    me = seats[pid]
    alive = [q for q, p in state.players.items()
             if not p.is_neutral and p.alive and q != pid]
    riv = max((seats[q] for q in alive), key=_Seat.material, default=_Seat())

    tot_inc = sum(q.inc for q in seats.values()) or 1.0
    tot_ships = sum(q.ships() for q in seats.values()) or 1.0
    tot_sys = sum(q.nsys for q in seats.values()) or 1
    neutral_inc = neutral_ships = 0.0
    for s in state.systems.values():
        if s.owner_id == 0:
            neutral_ships += s.ships
            if s.production > 0:
                neutral_inc += 1.0 / s.production
    lanes = state.adjacency
    n_lanes = sum(len(v) for v in lanes.values()) or 1
    lane_turns = sum(t for v in lanes.values() for t in v.values()) / n_lanes

    return [
        me.inc / tot_inc, me.ships() / tot_ships, me.nsys / tot_sys,
        (me.inc - riv.inc) / tot_inc, (me.ships() - riv.ships()) / tot_ships,
        (me.nsys - riv.nsys) / tot_sys,
        me.near / tot_ships, me.mid / tot_ships, me.far / tot_ships,
        riv.near / tot_ships, riv.mid / tot_ships, riv.far / tot_ships,
        me.threat / tot_ships, riv.threat / tot_ships,
        me.exposed / tot_ships, riv.exposed / tot_ships,
        me.reach / tot_inc, riv.reach / tot_inc,
        neutral_inc / (tot_inc + neutral_inc),
        1.0 if me.contact else 0.0,
        len(alive) / 4.0,
        (len(seats)) / 5.0,
        len(state.systems) / 40.0,
        lane_turns / 10.0,
        min(state.turn, 200) / 100.0,
        config.DEFENDER_ADVANTAGE,
        max(config.COMBAT_JITTER, TUNED_JITTER) * 10.0,
        me.pipeline / (tot_inc * 3.0), riv.pipeline / (tot_inc * 3.0),
    ]


# --------------------------------------------------------------------------- #
# The learned value.
# Layout: input standardisation (MEAN, SCALE), then a list of dense layers
# (weight rows, biases), tanh between them, a logistic on the single output.
# --------------------------------------------------------------------------- #
# BEGIN WEIGHTS
MEAN: list[float] = [0.404522, 0.404522, 0.404522, -0.057885, -0.038057, -0.056897, 0.052128, 0.077006, 0.033401, 0.059292, 0.086123, 0.034597, 0.056777, 0.058744, 0.095186, 0.097137, 0.131921, 0.140415, 0.190555, 0.797387, 0.426555, 0.598331, 0.429472, 0.422265, 0.715798, 1.0, 1.0, 0.151084, 0.17188]
SCALE: list[float] = [0.24149, 0.209953, 0.241231, 0.425286, 0.353734, 0.424857, 0.087331, 0.111275, 0.085009, 0.09012, 0.113104, 0.085612, 0.099254, 0.099711, 0.132843, 0.133935, 0.22484, 0.229167, 0.24192, 0.401946, 0.227201, 0.208197, 0.19398, 0.140908, 0.557377, 1.0, 1.0, 0.114656, 0.110262]
LAYERS: list[tuple[list[list[float]], list[float]]] = [
    ([
        [0.368682, -0.106014, 0.138414, 0.313215, -0.05139, 0.063651, 0.255546, 0.288919, 0.023731, 0.114842, -0.056446, -0.135445, -0.314495, 0.272366, -0.004803, -0.191537, 0.062882, -0.424709, -0.141639, 0.300061, 0.170013, 0.267306, -0.019167, -0.088924, 0.296014, 0.0, 0.0, 0.148933, 0.098421],
        [0.520627, -0.097521, 0.080705, 0.696257, 0.473209, 0.267076, 0.144038, 0.100436, 0.093999, 0.159122, 0.198354, 0.024346, -0.032359, 0.406378, 0.027268, 0.248365, -0.082918, -0.071668, 0.583471, -0.144306, 0.369689, 0.322016, -0.541825, -0.228517, 0.08891, 0.0, -0.0, 0.020091, -0.009632],
        [-0.597825, 0.168766, 0.274483, -0.89708, 0.3155, -0.078773, 0.03418, -0.008576, -0.061611, -0.066446, 0.079671, 0.012207, 0.302115, -0.162015, 0.400341, -0.129128, -0.01465, -0.175885, -0.235421, 0.028474, -0.001492, 0.159357, -0.315084, -0.017565, 0.595362, -0.0, 0.0, -0.039624, 0.114714],
        [0.406577, -0.23567, -0.071449, 0.657096, 0.146962, 0.29085, -0.396817, -0.212818, -0.149614, -0.072301, -0.043892, -0.083552, -0.402976, -0.028853, -0.015621, -0.21578, -0.115734, 0.076224, -0.294723, 0.086187, -0.011377, -0.289098, 0.416791, -0.000551, 0.314063, -0.0, -0.0, 0.131498, 0.100467],
        [0.464551, -0.339966, 0.277159, 0.507947, -0.061602, 0.411763, 0.172677, 0.144658, 0.118904, -0.137899, -0.117495, -0.077315, 0.01904, 0.058515, -0.266019, -0.110801, -0.393384, 0.10605, -0.050103, 0.01493, -0.002089, 0.445063, -0.652066, 0.511333, 0.454355, -0.0, -0.0, 0.131075, -0.044298],
        [-0.376196, -0.300352, 0.125491, -0.493971, -0.065934, 0.000722, 0.060042, -0.073759, 0.0343, 0.236105, -0.182718, -0.281003, 0.017187, -0.252887, -0.302776, -0.038659, -0.347015, -0.16855, 0.121344, 0.306018, -0.019501, -0.056711, 0.113944, -0.109064, 0.317282, -0.0, -0.0, 0.049161, 0.240205],
        [0.193136, -0.610853, 0.018903, 0.324312, -0.628643, 0.044247, 0.005703, -0.037952, -0.059439, 0.23905, 0.402574, 0.29607, -0.425597, 0.042363, 0.019278, -0.031369, -0.163362, -0.146669, 0.01189, 0.604368, -0.080832, 0.341918, -0.255977, 0.344448, -0.128421, 0.0, -0.0, -0.012792, 0.050736],
        [-0.361042, -0.065171, 0.074433, -0.484918, -0.006317, -0.01204, 0.186469, 0.125939, 0.063411, -0.03257, -0.157303, -0.052768, 0.260031, -0.231, 0.171724, -0.326151, 0.221656, -0.016562, 0.204313, -0.446298, -0.225816, -0.251889, 0.307156, -0.041063, -0.535661, -0.0, 0.0, -0.10785, 0.081256],
        [-0.517459, -0.450702, 0.111458, -0.450123, -0.312291, 0.345955, -0.223836, -0.314637, 0.161328, 0.136157, 0.132466, -0.028565, 0.233597, 0.215619, 0.224835, -0.130383, 0.149299, 0.101134, 0.103966, -0.17156, 0.128885, -0.111128, 0.112429, 0.383357, -0.092773, -0.0, -0.0, 0.192549, -0.171377],
        [0.230693, 0.212505, -0.184031, 0.296947, 0.170976, -0.066212, 0.076586, -0.954503, 0.059431, -0.237361, -0.130253, -0.104844, 0.112005, -0.196083, -0.149872, 0.209829, -0.034373, -0.215364, 0.154536, -0.146955, -0.15603, 0.061397, -0.225981, -0.390916, 0.047514, 0.0, -0.0, 0.121478, 0.185776],
        [-0.013898, -0.010333, -0.015245, -0.026952, -0.017035, -0.026524, 0.007037, 0.005375, -0.033773, 0.021134, 0.035548, -0.035655, -0.005167, 0.047701, -0.030406, 0.030758, 0.040372, 0.003842, -0.036101, 0.057191, 0.001935, 0.028636, -0.050355, -0.035668, 0.010846, -0.0, -0.0, 0.015283, 0.0127],
        [0.157337, 0.227608, -0.184861, 0.353778, 0.483892, 0.226477, 0.047785, -0.049214, -0.09687, -0.203573, -0.246963, -0.092939, 0.107657, 0.178423, -0.200141, 0.197954, 0.260747, -0.034138, -0.351915, 0.096824, -0.101339, -0.322245, 0.459239, 0.427675, 0.02435, 0.0, 0.0, -0.120831, -0.022379],
        [0.652305, -0.592062, -0.010781, 0.305112, -0.923393, -0.388916, -0.090586, -0.096563, -0.099294, -0.098387, -0.031281, -0.048755, 0.051774, 0.213182, 0.032787, 0.129367, 0.106156, 0.03925, -0.660996, 0.07142, -0.138051, -0.227388, 0.07226, 0.018182, 0.198327, -0.0, -0.0, -0.14122, 0.140808],
        [0.074842, 0.056039, 0.306852, -0.569248, -0.363195, -0.54239, -0.131509, -0.185026, -0.087669, -0.037676, 0.015132, 0.049952, -0.0222, -0.292055, -0.101633, -0.171392, -0.088981, 0.316995, -0.30633, 0.205669, 0.069228, 0.117234, 0.943141, 0.187045, 0.397265, -0.0, -0.0, -0.050878, 0.257421],
        [0.173705, -0.210858, 0.172477, 0.149152, -0.447389, -0.00544, -0.419686, -0.607343, -0.463557, 0.055584, 0.075644, 0.102973, -0.012652, 0.220492, -0.027502, 0.049094, 0.076732, 0.090226, 0.021163, -0.321677, -0.263741, -0.095095, 0.180541, -0.272452, -0.175754, -0.0, -0.0, 0.100787, -0.067097],
        [0.532327, -0.129956, 0.081623, 0.593999, 0.00052, 0.296466, 0.207401, 0.255281, 0.234123, -0.099792, -0.139922, -0.030399, -0.205422, 0.027199, 0.058831, 0.143359, 0.101144, 0.201032, -0.377244, -0.034887, -0.141706, 0.210332, 0.677735, -0.020352, -0.381538, -0.0, 0.0, -0.012345, -0.091631],
    ], [0.411405, 0.772392, 1.088087, -0.707054, 1.068633, 0.63999, 0.087407, -0.68627, -0.691948, 0.017907, -0.018323, -1.340989, -1.113046, -0.573593, 0.478876, -1.333996]),
    ([
        [0.440969, 0.677989, -0.821281, 0.674716, 0.601623, -0.444204, -0.758294, -0.709659, -0.549567, 0.369869, -0.012794, 0.942249, -0.812567, -0.700962, -0.765341, 0.877664],
    ], [-0.308621]),
]
# END WEIGHTS



def value(state, pid: int) -> float:
    """``pid``'s win probability, in [0, 1]. A decided board is certain."""
    if state.winner is not None:
        return 1.0 if state.winner == pid else 0.0
    return forward(features(state, pid))


def forward(x: list[float]) -> float:
    h = [(v - m) / s for v, m, s in zip(x, MEAN, SCALE)]
    last = len(LAYERS) - 1
    for i, (rows, bias) in enumerate(LAYERS):
        h = [b + sum(w * v for w, v in zip(row, h)) for row, b in zip(rows, bias)]
        if i < last:
            h = [math.tanh(v) for v in h]
    z = max(-30.0, min(30.0, h[0]))
    return 1.0 / (1.0 + math.exp(-z))
