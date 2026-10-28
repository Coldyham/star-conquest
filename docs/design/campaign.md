# The weekly campaign: crowns, embargoes, the grace period

The board's campaign meta-map and the records around it: crowns, embargoes
and the rules in full; the grace period after a lost neighbour; and the
campaign fleets proposal (decided against). Split out of
[`leaderboard.md`](leaderboard.md); the tables are in [`schema.md`](schema.md).

## Crowns, the weekly campaign and embargoes

- **Crowns reward stealing a record, not volume.** `crowns.html` ranks players
  by contested maps whose record they hold (`crown_holders`) and counts, per
  Monday-to-Monday UTC week, scores that strictly beat somebody else's record
  (`crown_steals`); a tie never steals, the earliest holder keeps it, as
  `game_summary` credits. Both read `counted_scores`, the one place the rule
  lives, and so does `game_summary`'s best (the card's record, "Bot leads",
  the average best turns) and the map page's "Play this map" target: a `verified` score counts, an unchecked or `missing` one that names a
  replay counts for its first 24 hours, one posted before 2026-10-15 counts
  unless it is a `mismatch` under current rules, and only the first post of a
  match, or of a game however it is re-uploaded, counts (reasons:
  [`schema.md`](schema.md), "counted_scores and the crowns").
  Nothing is stored, so moderation recomputes them for free.
  `js/crowns.mjs` only orders rows; `tests/test_crowns_sql.py` runs the SQL
  against a real Postgres when `SC_TEST_PG` is set.
- **The weekly campaign stores its map and derives its state.**
  `tools/campaign.py` (the hourly worker; a no-op once the week's row exists)
  writes one `campaigns` row per Monday-to-Monday UTC week: field nodes laid
  out by `mapgen` with half again a game board's extra lanes
  (`FIELD_EXTRA_EDGE_FRACTION`), each an unplayed seed on an existing non-hand-drawn config
  of at most `config.STANDARD_MAX_NODES` systems (`FAMILY_MAX_NODES`: one
  120-system test game was enough to put big maps in a week, and a big map is
  a long sitting for one node) (sometimes its symmetric variant, plus one or two "?" nodes rolled with
  `settings.randomise_knobs` — the menu's dice, so never below a
  `settings.ROLL_FLOORS` entry (sight 0 is opt-in only), and a played config
  below one is never a node either; a symmetric node whose config names no `layout`
  rolls one of `mapgen.SYMMETRIC_LAYOUTS`), and a ring of homes, one lane each off the edge
  nodes `mapgen.peripheral_starts` picks. A *cut node* (`cut_nodes`: one whose
  loss splits the field, so a single par-tight score there walls off what lies
  behind it) gets one of the week's biggest maps and never a "?"; meshing the
  field until there were none (1.2) cleared them but turned a big week into a
  web, so the few left are made the hardest to lock down instead. A node's `settings` is stored in the
  pruned `token_dict` form `games.settings_json` holds, and `campaign_games`
  matches it to its game by seed plus jsonb *equality* — never
  `sc_config_key`, whose text digest tells Python's `0.0` from the `0` a
  browser posts, and so misses nearly every node. `campaign_scores` sits on
  that view, and so do the green campaign badge on a map's page and index row
  and the link back to `campaign.html?node=`. Who holds what is never stored: `js/campaign.mjs`'s `fold` replays the
  week's hand-played counted scores in posting order — a home goes to the
  first win from a player without one and can't be taken; a field node falls
  to a win posted while holding a neighbour (or within `GRACE_MS` of losing
  one, or the node itself), and a held one only to a strictly better score; see "The grace
  period" in this file. A bad week is remade by deleting its row
  (`tools/admin.py delete-campaign`, audited with the old graph) and letting
  the worker run again; scores already posted on its nodes stay on their maps
  but stop counting toward the week. The Advanced slider ranges live in `settings` (`ADV_*`) for
  this reason; `menu` aliases them.
- **A map can be registered with no score at all, and can carry a one-time
  reveal date over its board.** `js/submit.mjs`'s `ensureGame` accepts any
  Star Conquest link, not just a challenge one — `token-decode.mjs`'s
  `decodeToken` returns `challenge: null` for a plain settings-share link (or
  one carrying `Challenge`'s own `turns <= 0` sentinel) rather than rejecting
  it, and the site takes that as a setup to add rather than a score to post.
  That is what lets a setup be shared and played before anyone — its own
  author included — has a result on it to disclose. `games.embargo_until`
  rides along on that same insert, optionally, and only there: `games` is
  append-only like everything else on this board, so an embargo can only
  ever be decided the one moment a map's row does not yet exist, never
  retrofitted onto one already on the board.
  - **It gates the replay and the detail behind it, never the fact that a
    lead exists.** `public_replays` filters out a match whose map is still
    embargoed — the DB-level half, real for every caller, not just the
    site's own UI — and `game.mjs`'s `renderEmbargoed` is the other half: it
    never requests the per-score list at all while a map is embargoed, only
    `game_summary`'s own aggregate (`best_turns`/`best_user_name`/
    `best_holders`/`score_count`), so there is nothing for the page or its
    network tab to hand out beyond who is ahead and by how many turns. Lost,
    hand and submission time stay off the page entirely, along with every
    other score — a stricter cut would leave nothing to chase, which
    defeats a deadline built to be raced against. `home.mjs`'s card makes
    the same cut on the list. Scores and rankings themselves are otherwise
    unaffected by an embargo — they post and rank normally throughout,
    since there is no account system here to tell a submitter's own later
    read apart from anyone else's, so the only boundary that can be
    enforced for everyone alike, the setter included, is on *how* a score
    was made rather than on whether one exists.
  - **A campaign node is embargoed until its week ends, and that is derived,
    never stored.** `game_embargoes` (schema.sql) is the embargo in force:
    the latest of `games.embargo_until`, the end of any live campaign week
    the map is a node of (matched through `campaign_games`), and the end of a
    live day the map is the daily of (`daily_games`, [`daily.md`](daily.md),
    "The day's winner"). `public_replays`
    and `game_summary.embargo_until` both read it, so every page that honours
    an embargo honours this one with no JS of its own. It cannot be stamped on
    the row instead: a node's seed is fresh, so its `games` row is created
    mid-week by whoever posts first, and `games` is append-only after that.
  - **Once it lifts, the map page names who was ahead at that moment.**
    `game.mjs`'s `liftNote` and the row's *Led at reveal* mark: the best
    counted score posted by then (`standings.mjs`'s `leaderAt`), and who has
    beaten it since, if anyone. The moment is the later of the stored reveal
    and the end of the last campaign week the map was a node of
    (`embargoLiftedAt`), read off the `campaign_games` row the page already
    fetches rather than added to `game_embargoes`, which reads live weeks only
    so `game_summary` never scans every past campaign. Counted by today's
    verdicts, so a score whose replay later fails its check was never ahead.

## The grace period

One timer in `js/campaign.mjs`, derived like everything else in the
campaign: `fold` records when each player lost each node (`lostAt`), and
nothing about who holds what is stored. The principle: **a game started while
you had access to a node should count**, as long as you post it soon after
losing that access.

- **`GRACE_MS` (30 minutes).** A field win counts if the player held the
  node or a neighbour at any moment in the 30 minutes before posting it. It
  fixes the case that made the rule feel unfair: you start a node beside one
  you hold, somebody takes that neighbour mid-game, and your win counted for
  nothing. Thirty minutes is about one game. It only needs to cover the game
  in progress when the neighbour went, not a long campaign of play from
  memory.
- **The node itself counts too.** Holding the node is access just as holding
  a neighbour is. So a holder replaying their own node to raise the bar, who
  loses it mid-game and has no neighbour left, can still take it back inside
  the grace.
- **Only for a game started with access.** The grace runs from the loss, but
  a game *started* after the loss isn't the case it exists for, so it gets
  none. `fold` can't see when a game started, so the game says: at Start it
  stamps the answer `/api/campaign` gave (`campaign.stamp`,
  `Ui.campaign_start`), which rides on the score as `Challenge.campaign` and
  is stored in `scores.campaign_start`. `startedWithAccess` decides what a
  stamp means: `grace`, `late-start`, `not-adjacent` and `no-home` were no
  access; `adjacent` and `own` were access. Starting inside a grace is not
  access, or a restart five minutes after the loss would carry it.
- **A stamp is a claim, and safe as one.** The grace still runs from the loss,
  so a stamp can only narrow it: a forged one gets exactly the rule without
  stamps, never a later move. That is why it needs no signature, and why a
  blank or unknown stamp is taken on trust: every score from before the column,
  every desktop game (no lookup), a lookup that hadn't answered by Start, a
  resumed save, and a hand-written link. Trusting those also keeps past weeks
  folding the way they did.
- **Which game is asking.** `attemptStatus` takes the game's stamp
  (`start`), or `starting` for a game about to begin. Either one without access
  turns `grace` into `late-start`, not a move. The menu asks with
  `starting=1`; the game under way asks with its stamp; the page asks with
  neither, since it can't know about a game in progress, and its grace line
  says a game already started still counts and one started now won't.
- **Rewinds keep the stamp; a new start takes a new one.** A mid-game rewind
  is the same match (`apply_rewind` keeps `campaign_start`). A Retry, or
  playing on from a finished game's history, is a match begun now and is
  stamped from the current lookup. A match forked out of a watched replay gets
  a blank, since nothing looked its setup up while it was watched.

### Decided against: an "able to capture" flag on its own

Before the grace, the idea was to bake "could capture this node" into the link
when the game was started, and let the board honour it. It was dropped because
a link can be kept: start a game while next to a node, keep the link, and post
a win days after losing the neighbour. On its own a flag is a standing permit.
Combined with the grace (above) it can only narrow a clock that is already
running, which is what made it safe to add.
- **The edge minute counts on both sides.** `fold` accepts a win posted
  exactly `GRACE_MS` after the loss, and `attemptStatus` says yes at that
  minute too, so the page never says no to a win that would count.
- **`attemptStatus`** is the one place that says what a player may do on a
  node at `now` (`why`, `graceUntil`, `beat`). The page and the game both read
  it, so they can't disagree about a timer.

### Decided against: a cooldown between moves

Built and then removed, the same week. Each move started an hour's wait
before that player's next one, and a win posted during the wait was queued
and played when the hour was up. The aim was to slow a player sweeping the map
from Monday 00:00 UTC, and the queue meant no game was wasted.

It went because two timers together read as nonsense. A player inside their
grace with their cooldown still running was told "post within 12 minutes" and
"it plays in 40". That was consistent, since the grace was judged at posting
and the queue was the rule's doing, but nobody would read it that way. A
player who can't claim for 40 minutes should not look like they can take
the node at all. The remedies were a second rule (judge the grace when the
queued win plays) or more wording, and both made the campaign harder to
explain than the sweep made it unfair.

What paces a sweep now is only how long a game takes, roughly two nodes an
hour per player. If that ever turns out to be a real problem, look for a brake
that is *one* clock, not a second one beside the grace.

### In the game

The game and the board are one site, so the game can ask before it plays.

- **The game never holds the rules.** `netlify/functions/campaign.mjs` takes
  the setup's settings token and the player's name, finds this week's node by
  value (`setupIdentity`, as `campaign_games` matches by jsonb equality), and
  returns `attemptStatus` and the holder. A Python port of
  `fold` would be a second copy to drift, with nothing like
  `test_leaderboard_sync` able to pin a rule set. The function reads the public
  rows with the publishable key, so it holds no secret.
- **Times are the server's, counted on ours.** The answer carries the server's
  `now`, and `campaign.Status.left_ms` counts down from when it landed with
  `time.monotonic`. A device with its clock set wrong still shows the right
  minutes.
- **Web only, and never in the way.** The name is the board's own
  (`sc_pbp_name`, shared by origin), which a desktop build doesn't have. A
  lookup waits `SETTLE_S` after the last setup change, refreshes every
  `REFRESH_S` (so a neighbour lost mid-game starts its countdown), asks again
  at once when the game it asks for changes (`Watcher.pump`'s `start`), and a
  changed setup drops the old answer at once so Start never confirms from
  another map's answer. A Start pressed before the answer lands just starts,
  because a confirm is a courtesy, not a gate.
- **One modal, not two.** The confirm reuses the slow-setup modal
  (`menu._unless_slow`) with the node as its title, and puts the campaign's
  lines ahead of any bot warning. It asks only when a win wouldn't be a move
  (a node lost before Start now reads `late-start`), or would be one only
  inside the grace (`campaign.confirm`). An open node, your
  own node, or a player with no name yet starts straight away.
- **The top bar label is text `main` writes** (`main.campaign_tick` into
  `Ui.campaign_label`). `campaign` imports `pbp`, which imports `engine`, and
  `render` must never pull that in, even indirectly.
- **The board says it before the game does.** game.html on a live node shows
  the campaign page's own lines above "Play this map" (`attemptLines`, shared
  with campaign.html's node panel), so a player arriving from the board already
  knows whether a win would be a move. It is a note, not a confirm: a modal on
  the board would ask a second time for one game, since the game confirms on
  Start, and the link has no way to say the player already answered.
  `weekQueries` is the one copy of the two reads a week is folded from, for the
  two pages and the endpoint.

## Campaign fleets (decided against)

**Decided against (2026-10-26):** the grace period, as built, is the answer.
A proposal for giving the campaign meta-map real-time lanes and fleets, written
up so it can be argued over and playtested before any of it is built. Nothing
here is in the code. The rules in force are in "Crowns, the weekly campaign and
embargoes" in this file.

Since this was written, "The grace period" in this file has dealt more
cheaply with the stolen neighbour. A cooldown for the Monday sweep was tried
and dropped (same section, "Decided against: a cooldown between moves"), so
fleets' remaining case is the sweep and the warning, and they still have to
justify a stored table and a per-person credential.

### The problem

`fold` (`js/campaign.mjs`) asks one question of a win on a field node: did this
player hold a neighbour *at the moment the score was posted*?

```js
if (![...links.get(nodeId)].some((other) => holds(key, other))) continue;
```

A score that fails is dropped with no trace. It is never banked against a later
neighbour, and `campaign.test.mjs` pins that ("a field node is taken only from
next to something you hold"). Three things follow:

- **A neighbour stolen mid-game voids the game.** You start a node while you
  hold its neighbour, someone beats your score on that neighbour before you
  post, and your win counts for nothing. Nothing you could have seen warned you.
- **No warning of an attack.** A holder learns their node has gone only when
  the score that took it is already posted.
- **The week can be swept at the start.** Nothing paces a run of captures but
  how fast you can play, so one keen player starting at Monday 00:00 UTC can
  take a chain of nodes before anyone else has opened the page.

### The model: a launch is a move, and a move is a row

Each player has a fleet, which is a claim in transit along one lane of the
meta-map.

- **A new append-only table**, `campaign_launches (week_start, from_node,
  to_node, user_id, launched_at default now())`. The server stamps
  `launched_at` and the client can never set it. Like `scores`, a row is a
  claim, not a fact: **any row is accepted, and `fold` ignores invalid ones.**
  That keeps "who holds what is never stored" true. The standing is still a
  replay, now of launches plus scores, so moderation still recomputes it for
  free.
- **A launch is valid** when, at `launched_at`, the player holds `from_node`,
  the two nodes share a lane, and the player has no other fleet in flight or
  in its window. One fleet per player is the main brake on a sweep.
- **Arrival** is `launched_at + hours` for that lane, and it opens a window of
  `WINDOW_HOURS`. A counted, hand-played win on `to_node` posted inside the
  window is a move. **Losing `from_node` after launch does not cancel it**, so
  the claim is locked at launch, and that fixes the stolen-neighbour case. You
  start a game knowing whether it can count.
- **A window that closes with no win just ends**, and the player may launch
  again. Recall and retreat are left out. They would add a rule without
  adding a choice anybody needs.
- **Homes stay as they are.** No fleet is needed to claim an empty home. A home
  is where a player's first launch starts, and it still can't be taken, so it
  is still the way back for anyone who loses the field.

What a fleet does *not* change: the bar to take a node. An empty node falls to
any qualifying win, and a held one only to a strictly better score
(`compareScores`, so a tie defends).

### Collisions fall out of the existing rules

Fold launches, arrivals, window closes and scores as one timeline, in time
order. When two players' windows overlap on one node, the first counted win in
either window takes it if it is empty. After that, a win in the other window
must strictly beat the new holder. In effect the best score wins, and the
earlier one wins a tie. That is the same thing that happens today when two
neighbours race for a node, so no new rule is needed.

### What the warning is, honestly

The campaign page would show every fleet in flight, with its source, target
and ETA. A holder's answer is the one the rules already give: better your own
score on the node to raise the bar an attacker must clear.

It warns of a *claim window*, not of play. An attacker can play the node's map
whenever they like, before launching or during the flight, and post when the
window opens. The embargo is what stops them copying the holder's replay, so
the warning hands over no information beyond the target and the time.

### Lane length

Hours per lane come from the node coordinates `tools/campaign.py` already
writes (`x`, `y`, in mapgen world units), scaled by `HOURS_PER_UNIT`. Store
them in the graph rather than having the page recompute them, so rounding is
decided once. Lanes become `[a, b, hours]`. `neighbours()` destructures only
the first two, so it reads the new form unchanged. Bump `GRAPH_VERSION` to 2,
and keep folding version-1 weeks under the current rules so past weeks don't
change.

The knobs to playtest are `HOURS_PER_UNIT`, `WINDOW_HOURS` and fleets per
player. Back of the envelope: with one fleet and a mean lane time of T hours, a
player makes at most about 168 / T moves a week. At T = 12 that is 14 moves on a
12-to-40 node field, few enough that latecomers still arrive to a contested
map. Measure the real distribution of lane lengths on a few generated weeks
before choosing the scale. Homes sit `HOME_OFFSET` out, which is longer than many
field lanes, so they may want their own fixed time.

### The open problem: identity

Names are not identities ("Known limitations, accepted on purpose" in
`leaderboard/README.md`), and today that is nearly harmless: a score forged in
your name can only help you. A forged *launch* does real damage. It spends your
one fleet and sends it where you didn't want it to go, and the window it opens
blocks your next launch.

A candidate, not a decision: a per-week campaign token, minted on your first
home claim, kept in browser storage the way the game keeps `sc_pbp_seats`, and
stored hashed on the server. Launches would go through a Netlify function that
checks it, holding the only write key the way `pbp.mjs` and `log.mjs` do. The
cost is that it would be the board's first per-person credential, with the
device-bound trade play-by-post already makes: a lost token is a lost fleet
until the week ends, unless `tools/admin.py` learns to reissue it.

### What building it would touch

For a later plan, not this one: `schema.sql` (the table, its RLS and grants, a
view for the page to read), a new function in `netlify/functions/`, `fold`
(the merged timeline, behind a version-2 branch) and `canAttempt` in
`js/campaign.mjs`, the campaign page (a launch control, fleets in flight),
`tools/campaign.py` (lane hours, `GRAPH_VERSION`), and tests in
`leaderboard/tests/campaign.test.mjs` and `tests/test_campaign.py`.

### Decided against: a play-by-post duel on a collision

When two fleets meet at one node, the obvious game answer is to make them
fight: open a play-by-post match between the two players and give the node to
its winner. Set aside, for three reasons:

- **It is a different game.** A node is a challenge against bots on one fixed
  map. A match between two people is another setup on another board, so its
  result says nothing about the node.
- **Somebody may never turn up.** A duel needs both players. The best-score rule
  needs neither to wait on the other.
- **It drags play-by-post's deadline machinery into the campaign**, where a
  week-long clock is already running.

The best-score rule settles a collision with what already exists.
