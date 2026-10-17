# Docs index

The design notes in `design/` hold the *why* behind the rules in `CLAUDE.md`:
the reasoning, the measurements, and the alternatives that lost. This page says
which file covers what. Two companion pages are for grepping, not reading whole:
[`sections.md`](sections.md) lists every design file's sections, one line
apiece, and [`decided-against.md`](decided-against.md) lists every idea that was
built or proposed and then dropped, so a new session can check an idea before
rebuilding it.

## Using these docs

- **Before proposing a mechanism, a bot tactic or a re-tune, grep
  [`decided-against.md`](decided-against.md)** and `design/` for the
  constant, function or idea you would touch. Most ideas that look obvious have
  already been built and measured, and many measured null or worse.
- **Before running a bot measurement, read "Measuring a bot" in
  [`design/bots.md`](design/bots.md).** It lists the method mistakes earlier
  sweeps made.
- `CLAUDE.md` states *what* each rule is. A design file's headings match the
  `CLAUDE.md` bullet they explain, so the heading text is the key to search for.
- Read a design file when you are changing that code, not as background, and
  prefer the section you need (find its heading in [`sections.md`](sections.md))
  to the whole file.

**Adding to them.** Put a section in the file for its area, under a heading that
matches the `CLAUDE.md` bullet. Record a negative result as carefully as a win:
what was built, the numbers, why it lost. Then add its line to
[`sections.md`](sections.md), and to [`decided-against.md`](decided-against.md)
if it lost. Point to another
file's section by file and heading (`marshal-pricing.md, "Garrisons run
away"`), never with "above"/"below". Code comments do the same
(`see "Denying the swap" in docs/design/marshal-flow.md`). Keep each file under
~1000 lines. When one grows past that, split it by topic and update this page,
`sections.md` and `CLAUDE.md`'s list of files.

## Files

| File | Covers |
| --- | --- |
| [`design/rules.md`](design/rules.md) | Every `CLAUDE.md` rule in full, under the same headings, with the functions and tests it names (grep it; don't read it whole) |
| [`design/core.md`](design/core.md) | Settings tokens, the random seat, challenge links and keeping digests stable, ship-speed growth, in-lane battles, the replay log, star names |
| [`design/shell.md`](design/shell.md) | Text sizing, touch targets, viewport margins, the send popup, forwarding rules, the menu's Combat page, spectating, ironman, route mode |
| [`design/turnfilm.md`](design/turnfilm.md) | The animated end of turn (`turnfilm.py` and the shell side of playback) |
| [`design/hand-maps.md`](design/hand-maps.md) | The hand-drawn map recipe and the map creator |
| [`design/leaderboard.md`](design/leaderboard.md) | The offline bot column, checked scores, watching a replay, replay versioning, playstyle readings |
| [`design/setup-families.md`](design/setup-families.md) | Grouping setups on the board: pace on every card, and the "By family" view above By game and By config |
| [`design/schema.md`](design/schema.md) | The board's database (`leaderboard/schema.sql`): the trust model, upgrading by re-pasting, each table and view's shape, the grant trap, the play-by-post tables |
| [`design/par.md`](design/par.md) | The par search, a local check: the floor, lucky and honest dice, what a rewind does to the rng, readings against human wins |
| [`design/seat-play.md`](design/seat-play.md) | Six games played by hand against marshal and actuary with `tools/seat_play.py`: what decided them, strikes on emptied systems (people refill, bots do not), marshal's price against a seat that stands, and a person's brief |
| [`design/pbp.md`](design/pbp.md) | Play-by-post |
| [`design/bots.md`](design/bots.md) | The roster as a whole: measurement method, the parameter space, real-game positions, replaying bots for the board, break-even margins, defender advantage, the bot maker, non-Python bots |
| [`design/knower.md`](design/knower.md) | knower: the oracle, the search, its horizon, its cost |
| [`design/council.md`](design/council.md) | council: knower's Search with a learned judge, actuary as an adviser, and each adviser playing its own line; the measurements, the cost, what was dropped |
| [`design/marshal.md`](design/marshal.md) | marshal: what it was built on, **the current roster ladder**, the 2026-09 constant sweep |
| [`design/marshal-pricing.md`](design/marshal-pricing.md) | marshal: what a strike or a defence is priced against |
| [`design/marshal-flow.md`](design/marshal-flow.md) | marshal: where the surplus goes, plus the successor fixes and FEED |
| [`design/marshal-joint.md`](design/marshal-joint.md) | marshal: which constants move together, how the phase order couples them, the coupling census, joint sweeps, and the guard split into a strike floor and a pour floor |
| [`design/actuary.md`](design/actuary.md) | actuary: the projected ledger instead of phases, its cost, where it stands, what the measurements changed; the planned opening (Opening: Planned) |
| [`design/learner.md`](design/learner.md) | learner: each rival's habits read off the board and kept turn to turn, memory keyed by the game's path, how well it predicts the roster's launches and a person's, the gate it did not pass, the bot built on actuary anyway, and the improvements proposed for it |
| [`design/learning.md`](design/learning.md) | actuary's third stop (Style: Learning): the learned strike curve in the risk term, each garrison next door priced as striking whole or not at all, and the arms tried on it |
| [`design/convoy.md`](design/convoy.md) | convoy (built, measured, not shipped; code at `7153065`): supply and demand over time, launching only what must leave now; where it stands, how differently it plays, what the measurements changed |

**Not design notes.** [`bot-api.md`](bot-api.md) (the wire protocol) and
[`bot-brief.md`](bot-brief.md) (a brief a player pastes into an AI assistant)
are written for outside developers building their own bots, and
`tests/test_bot_brief.py` checks the brief against the code. Treat them as
published documents, not working notes. [`site-merge-plan.md`](site-merge-plan.md)
is the planning brief for merging the game and board sites. That merge is
implemented, and the brief is kept for its reasoning.
