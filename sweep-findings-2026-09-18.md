# The built-in heuristic's `AiParams` defaults (2026-09-18)

A paired A/B sweep of every `AiParams` field the built-in heuristic reads, run
through `tools/sweep.py`. 46,400 games in `results/ledger.jsonl`.

**Nothing here is proposed as a change to `config.AI_*`.** See "Why the defaults
stay where they are" at the end — the reason is stronger than a moved digest.

## Method

Each arm is one perturbed config duelled against the stock heuristic, two seats,
every seed played twice with the seats swapped. With deterministic bots that
mirror cancels seat advantage *structurally*, so an arm equal to the baseline
reads exactly 50.0% rather than approximately — the harness asserts this on a
built-in null control before it will run anything.

Four cells, chosen so a knob keyed off travel distance is live in at least one:
`random:18` and `random:24` at the default 6 ly/turn, `random:24` at 12, and
`random:18` at 3. Symmetric duels were tried and rejected by the harness's own
gate — 31 of 40 smoke games timed out, and a rate out of a cell like that is "of
the games that ended", a biased sample.

Every row below is **reproduced**: the two independent halves of the seed range
agree in direction and the pooled 95% interval excludes 50%. The reserve rows are
additionally reproduced at two sample sizes, 200 seeds and 400.

## Sweep 1 — one knob at a time (32,000 games, 400 seeds)

    arm                             W-L      n    rate      95% CI      z   status
    reserve_fraction 0.25 -> 0.15  1857-832  2689  69.1%  [67.3, 70.8]  19.77  REPRODUCED
    reserve_floor    2    -> 1     1545-1023 2568  60.2%  [58.3, 62.0]  10.30  REPRODUCED
    expand_margin    1.3  -> 1.1   1310-1233 2543  51.5%  [49.6, 53.5]   1.53  not significant
    attack_margin    1.5  -> 1.8   1210-1192 2402  50.4%  [48.4, 52.4]   0.37  not significant
    reinforce_margin 2    -> 4     1290-1282 2572  50.2%  [48.2, 52.1]   0.16  not significant
    reinforce_margin 2    -> 3     1284-1274 2558  50.2%  [48.3, 52.1]   0.20  not significant
    attack_margin    1.5  -> 1.25  1328-1339 2667  49.8%  [47.9, 51.7]  -0.21  not significant
    reserve_floor    2    -> 3      907-1613 2520  36.0%  [34.1, 37.9] -14.06  REPRODUCED
    expand_margin    1.3  -> 1.6    940-1678 2618  35.9%  [34.1, 37.8] -14.42  REPRODUCED
    reserve_fraction 0.25 -> 0.35   592-1878 2470  24.0%  [22.3, 25.7] -25.88  REPRODUCED

Both reserve knobs move monotonically and in the same direction in all four
cells, at both speeds and both node counts. `attack_margin` and
`reinforce_margin` show nothing at any tested value.

## Sweep 2 — the two reserve knobs together (14,400 games, 200 seeds)

    arm                                        W-L      n    rate      95% CI      z
    reserve_fraction 0.0  + floor 0           1401-66   1467  95.5%  [94.3, 96.4]  34.86
    reserve_fraction 0.10 + floor 1 (or 0)    1167-247  1414  82.5%  [80.5, 84.4]  24.47
    reserve_fraction 0.15 + floor 1           1077-298  1375  78.3%  [76.1, 80.4]  21.01
    reserve_fraction 0.05                      982-384  1366  71.9%  [69.4, 74.2]  16.18
    reserve_fraction 0.10                      971-390  1361  71.3%  [68.9, 73.7]  15.75
    reserve_fraction 0.15                      929-417  1346  69.0%  [66.5, 71.4]  13.96
    reserve_floor 1 (or 0)                     781-516  1297  60.2%  [57.5, 62.8]   7.36

**The pair is superadditive**, the same shape bot-design records for
`FRONTIER_GUARD` + `ENEMY_NEAR`: 71.3% and 60.2% alone, 82.5% together. The
fraction is on a plateau from 0.15 down to 0.05 (69.0/71.3/71.9), so the
operative quantity is "well below 0.25", not any particular value.

Holding nothing back at all reads 95.5%. `_surplus` is only ever consulted for a
system that already passed the AI's own threat check, so "reserve nothing" still
means a threatened system holds its whole garrison — the knob is only deciding
what *unthreatened* systems do with ships that are otherwise idle.

## Two knobs with no travel below their default

Both were found by the harness refusing to measure them, not by a statistic.

**`reinforce_margin` cannot go below 2.** `ai.compute_orders` skips any system
where `_threat >= ships`, so everything reaching `_frontier_order` has
`self_deficit <= -1`; the reinforce branch needs `nbr_deficit >= 1`; so
`nbr_deficit - self_deficit >= 2` always. Values 0, 1 and 2 are the same
behaviour — confirmed bit-identical over 200 games in four cells. The default sits
exactly on the knob's floor.

**`reserve_floor` cannot go below 1 while `reserve_fraction > 0.`** `reserve =
max(floor, ceil(fraction * ships))`, and the only garrison size where `max(0, ...)`
differs from `max(1, ...)` is zero ships, which has no surplus either way. This is
why `floor 0` and `floor 1` returned bit-identical records above, twice.

## Why I stopped suspecting the 95.5%

It is a large enough effect to assume the harness first. Three things moved me:

* **It is not a timeout artefact.** Counting every timed-out game as a non-win,
  that arm takes 87.6% of *all games played* against the baseline's 4.1%, and it
  has the fewest timeouts of any arm (133 against the baseline-adjacent ~300).
  An arm inflating its rate by stalling would show the opposite.
* **It wins faster on a metric nothing was fitted to.** Median game length when
  `reserve_fraction 0.15` wins is 124 turns against the baseline's 138 in the same
  matchup; the turtling arm needs 167 when it wins at all. Turns-to-win is what
  the leaderboard scores on, and it was not part of any objective here.
* **The repo already contains the same finding, on a different bot, by a
  different route.** `models/marshal.py` sets `RESERVE_FLOOR = 0`, and
  bot-design records raising it to 1 or 2 as catastrophic (34.8%/25.0%/6.9% at
  18/24/40 nodes). Marshal was tuned to this conclusion independently. The
  built-in heuristic simply never received that treatment.

## Caveats, stated plainly

* **This is self-play only.** Every number above is against a copy of the stock
  heuristic. bot-design records a case where a knob gained 5 points in self-play
  and *lost 7* against thinker — the signature of tuning to a copy of yourself.
  The cross-opponent check against claudebot (the one roster bot that leaves the
  heuristic measurable headroom: 23.3%, where thinker is 2.9% and marshal 0.0%)
  was scoped out and **has not been run**. Until it is, none of this is
  established against the roster.
* **The `random:24` at 12 ly/turn cell is marginal**, running 30% timeouts over
  4,000 games. It agrees with the other three cells throughout, so it is not
  carrying any result on its own.
* **A harness gap this sweep exposed.** `tools/sweep.py` validates that every arm
  differs from the *baseline*, and its smoke checks that every arm's boards differ
  from the null's. Neither catches two arms that are identical to *each other* —
  which is exactly how `floor 0` and `floor 1` both passed every gate and then
  returned the same record. It was visible in the report only because the W-L
  columns matched exactly. Worth closing.

## Why the defaults stay where they are

Changing `config.AI_RESERVE_FRACTION` is not an option regardless of the size of
the effect, and the reason is worse than a moved `challenge_key()`.

`Settings.token_dict` prunes a seat's `ai` block from a shared link whenever it
equals `asdict(AiParams())` — the defaults *as of the reader's build*. Every
challenge link already in circulation therefore carries no `ai` block at all.
Move the default and those links do not merely fail a digest comparison: they
decode into a **different opponent than the one whose score they carry**,
silently. `settings._LEGACY_KEY_DROPS` cannot rescue this — that machinery
recovers digests across *added fields*, not changed default *values*, and there is
no equivalent for the latter.

The finding is reachable today without touching anything: `reserve_fraction` and
`reserve_floor` are per-seat sliders on the menu's AI tab, ranges 0.0-0.9 and
0-20, so a player can already set them. The other sanctioned route, if this is
ever wanted as a shipped strategy, is a drop-in `models/` bot, which moves no
default and no digest.
