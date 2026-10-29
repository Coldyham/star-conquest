# Leaderboard schema design notes

Why `leaderboard/schema.sql` is shaped as it is: the tables, views and grants
behind the board, the bot column, checked scores, the campaign and
play-by-post. The file's own comments say what each thing is and what breaks
if it changes. This file holds the reasons and the history. The features those
tables serve are explained in [`leaderboard.md`](leaderboard.md) and
[`pbp.md`](pbp.md), and `leaderboard/README.md` covers setup. Index:
[`../README.md`](../README.md).

## The trust model

No accounts needed. Anyone may read nearly everything and insert a user, a
game or a score. The public can never update or delete. Scores are
append-only, and "the best score" is a query, never a row that gets
overwritten. The one exception is opt-in: a person signed in with Google
(Supabase Auth) may claim one name nobody has used (`claim_name`), or the
owner assigns them one that is in use (`tools/admin.py assign-name`), and then
only they post under it (`leaderboard.md`, "Claimed names").

With RLS on, a command with no policy is refused outright, so the absence of
UPDATE and DELETE policies is the mechanism, not an omission. For `configs`
that is what makes the first name posted for a setup permanent. For
`config_tags` it is the whole design: a tag is meant to accumulate, which an
append-only table supports with no UPDATE policy at all. Removing or correcting
a bad row is the owner's job, through `tools/admin.py` under the secret key,
and `admin_actions` logs each command *before* it runs, with the rows it is
about to remove or overwrite. So a moderation can be undone by hand, and none
happens without a trace.

Four tables are written only by a worker and read by anyone: `bot_scores`,
`score_checks`, `campaigns` and `playstyle_readings` (read policy, no insert
policy, no insert grant). `game_logs`, `admin_actions` and the
play-by-post tables are closed entirely: RLS on, no policies, no public grants.
The secret key bypasses RLS, which is what lets a worker replace a
`bot_scores` row the public could never touch.

## Re-pasting upgrades a board

The file is pasted into the Supabase SQL editor, and pasted again after every
change, so it has to upgrade an existing board without touching a row. Hence
the shapes it repeats:

- `create table if not exists` silently skips a table that exists, so a column
  added later is an `alter table … add column if not exists` beside the table
  (`scores.match_id`, `game_logs.rules_version`, `bot_scores.log`). A column
  declared only in the `create` would never reach an existing board.
- A check that may later be tightened or widened is a `drop constraint if
  exists` / `add constraint` pair rather than a check inside `create table`.
  `score_checks_verdict` widened that way when `outdated` arrived, and
  narrowed again when it became a column, and
  `configs_tags_shape` was written so it could tighten.
- `create or replace view` accepts an existing view's columns unchanged in name,
  order and type, with new ones appended at the end. Insert one in the middle
  and Postgres reads it as a rename and refuses the whole statement. The old
  view stays in place, so the failure is silent from the page's side.
  `public_replays.rules_version` is the case that taught this: inserted ahead
  of `log`, the replace failed, every later query for `rules_version` errored,
  and `game.mjs`'s `watchableIds` swallows errors with `.catch(() => [])`, so
  every Watch link went, not only a new game's.

## games and its embargo

`game_key` is the game's own blake2s checksum of the setup
(`Settings.challenge_key`), so the board groups scores exactly as the game
decides two matches are the same. The checks on `mode`, `players` and `nodes`
are loose on purpose: `config.MIN_PLAYERS` and `MAX_NODES` are balance
constants free to drift, and policing them is not this table's job.

`games.embargo_until` is set only when the site inserts a *new* row, never by
an update, since there is no update policy for one to use. That makes it
permanent in both directions: nobody, the setter included, can move it, and
nobody can attach one to a map already on the board. An embargo has to be
decided before anyone has seen what it would hide.

It hides one thing, the moves, and never the score. A friend's turn count tells
you you're behind, not how they did it. With no accounts to check identity
against, a stricter blackout could only be enforced by hiding every reader
equally, which would also hide a submitter's own confirmation that their score
landed. So the line is drawn at the replay, the one thing a challenge like this
is trying to keep secret. The 90-day bound is a sanity cap, not a view on the
right embargo length. What the pages show while a map is embargoed is in
[`campaign.md`](campaign.md), "Crowns, the weekly campaign and
embargoes".

`game_embargoes` derives the embargo in force rather than storing it, because a
campaign node's seed is fresh (`tools/campaign.py`): its `games` row does not
exist when the week is written, it is created by whoever posts first from a
site that knows nothing about campaigns, and `games` is append-only after that.
There is no later moment to stamp it. Only live weeks are consulted, since a
lifted embargo reads the same as none and that keeps the scan to this week's
row however many campaigns pile up.

## scores

`hand` discloses autoplayed turns rather than disqualifying them.

`match_id` has no foreign key to `game_logs`. The log is posted by the *game*
and the score by the *site*, so either can arrive first, and a score whose
upload was blocked must still post. It is blank for every score from before the
game uploaded logs and for any hand-written link.

`submitted_at` is the one column the public may not write: its insert grant
is by column and leaves it out, so it is always the server's `now()`.
`counted_scores` trusts it for the legacy cutoff, the 24 hours' grace and
which post of a match or game came first, and `public_replays` trusts it to
tell the player's own upload from a later one. With a table grant, a backdated
score fell under the legacy rule and counted with no replay. A copy of someone
else's match backdated before theirs became the first post, and their score
stopped counting. The table grant is revoked before the column grant, because
revoking a table privilege drops its column grants as well.

`campaign_start` is a claim, like `hand`, and safe as one: the grace still runs
from the loss, so a stamp can only narrow it, and a forged one gets no more
than a blank. A blank is what every score before the column, every desktop game
and every hand-written link carries. See [`leaderboard.md`](leaderboard.md),
"The grace period".

## game_logs

Why the table is closed, what sends to it and why a row carries no identity
are in [`leaderboard.md`](leaderboard.md), "Checked scores". What is specific
to the schema:

- **The size bound is the real defence.** Measured whole games run 5-14 KiB
  encoded; the worst seen was a 4-player 24-node match of 295 turns at
  13.6 KiB. 256 KiB is generous for a very long game and still refuses a blob
  posted to fill the database.
- **`finished`, `won`, `hand` and `rules_version` are claims, stored as
  claims.** They index the logs (completed games, wins, games played by hand,
  replays that still reproduce) without decoding every blob. They are never
  evidence: replaying the log settles them, which is what
  `tools/verify_scores.py` does before a score is called verified.
- **`rules_version` defaults to 1 as a fact, not a guess.** Every row before the
  column existed was played under rules 1, the only version there had been.
- **Append-only shapes the key.** A checkpointing game lands several times,
  the longest row is the latest, and the index is `(match_id, turns desc)`.
- **The longest row is not evidence.** Anyone can upload under a `match_id` once
  a score makes it public, so the verifier tries the uploads from before the
  score first and records the one that verifies (`score_checks.log_id`).
  `public_replays` serves the log named for the match's first score, and
  `--prune` never deletes a named log. Before, an upload claiming more turns
  replaced a player's Watch link at once and got their own log pruned.

## score_checks

`missing` is stored rather than inferred from absence so the board can tell
"nobody has looked at this yet" (silence) from "we looked, and there is nothing
to check" (a fact about the score). `engine_rev` is `bot_replay.replay_rev`,
which leaves out `ai` and `models/`: a log records orders and dice, so a bot
changing cannot move it. An engine rule changing can, and that is what
`outdated` is for. Both are argued in [`leaderboard.md`](leaderboard.md),
"Versioning: bots are free to move, the engine is not".

`outdated` is a column beside the verdict, not a verdict. It started as a fifth
verdict, but whether a replay reproduces and whether the score is still
comparable with one posted today are separate questions, and every pairing
happens. A game dealt a lineup the setup no longer deals reproduces but isn't
comparable. A game from before a rules change that no longer reproduces is a
`mismatch` that is `outdated`: unverifiable, not wrong. The schema migrates
any stored `outdated` verdict by its detail.

A stored `verified` is never taken back (`verify_scores.settle`). A re-check
that no longer reproduces it marks it `outdated` instead. Scores from early
development were played against other bots, other rng rules and another
resolution order, and `RULES_VERSION` was not always bumped, so "it reproduced
under the code it was played under" is the only claim that survives. To judge
one afresh, delete its row.

`log_id` names the upload a verdict was reached on. A row without one is
decided again, which is how rows from before the column get one.

`game_digest` hashes every board the replay passed through (`replay.digest_hex`
per turn), so it names the game rather than the upload. A copy re-encoded with
a fresh `match_id`, a flipped `"ai"` flag or an order the engine drops passes
through the same boards and digests the same. It is `''` when the log didn't
replay. A `verified` row without one is decided again.

## playstyle_readings

Keyed by match, never by name, so a rename or a deleted score needs nothing
here, and a row exists only for a match in `public_replays`, so it reveals
nothing a visitor could not already work out from that replay. See
[`leaderboard.md`](leaderboard.md), "Playstyle readings".

## sc_config_key and sc_bots

Grouping by "same setup, different seed" needs a key, and a new field on
`Settings` would move `Challenge.key` for every map that ever existed
([`core.md`](core.md), "Keys outlive the schema that made them"). So this is
the other kind of key: computed in SQL from the stored `settings_json`, never
stamped by the game. The trade is that naming a config freezes it to the
function's body and to the game's pruning: see `leaderboard/README.md`, "Same
setup, different seed", for the cost and the hand remedy.

Both functions are `immutable` so they can back an expression index. Supabase's
linter flags them for a mutable `search_path`. That is a false positive: every
identifier resolves to `pg_catalog`, and pinning `search_path` would make
`sc_config_key` non-inlinable, which is what lets an expression index match it.

`sc_bots` is the one place the "missing, trimmed or blank seat reads as
`heuristic`" rule is implemented. The client reads the column rather than
re-deriving it.

## configs and config_tags

`configs` is first name wins, permanently, the same trade `users.name` makes.
It has no foreign key to `games` because `config_key` is derived, so a config
can be named before anyone plays it and naming one writes nothing to `games` or
`scores`. A name for a key nobody has played is harmless: `game_summary` only
surfaces a name that matches a stored setup.

Tags are meant to accumulate rather than be set once, but only from someone who
has posted a score. `config_tags.score_id` enforces that through its foreign
key, not an identity check (there is none anywhere on this board). `user_id`
rides along so `config_tag_counts` ranks by *distinct players*: one person
replaying a config ten times must not make a tag look like consensus.
`tag_key` is generated in the database, like `users.name_key`, so folding
"Fun", "fun " and "FUN" is not something every caller has to get right.

`config_tag_counts` folds in the old `configs.tags` array at a weight of one
per tag, with no player behind it. Without that, a board's existing tags would
have vanished the moment the view shipped, before any new-style tag existed to
replace them. Real tags outrank the flat weight as they accumulate. Nothing
writes `configs.tags` any more, and nothing deletes what is there.

The checks on `configs.tags` reject two things a naive length check lets
through: a nested array in the POST body, which would flatten under
`array_to_string`, and a NULL element, which passes `'' = any(tags)` (NULL,
not false) and then renders as nothing.

## bot_scores

Why the table has no public writer, why `won` and not `turns` decides, and why
a win stores its own replay are in [`leaderboard.md`](leaderboard.md), "Bot
replays". The schema-level choices:

- **One row per (map, bot), replaced by a rerun.** The result is a pure function
  of the setup, the seed and the code, so there is nothing to accumulate.
  `engine_rev` is provenance and what `--stale` re-derives from, never part of
  the key, so a map never shows two answers to one question.
- **`aux` and `aux_label` are recorded, not implied.** Every bot is replayed at
  the top of its own slider, so knower runs at Oracle: Search, a much stronger
  player than its menu default. A reader comparing the board against a game
  they played from the menu is owed which version answered.
- **`bot_timeouts`** is disclosed because a blown wall-clock budget makes the
  result depend on how fast the runner was that day.
- **The replay columns are blank together on a loss, never NULL**, so a plain
  `match_id <> ''` filter works in SQL and JS alike with no null handling.
- **`rules_version` defaults to 0, not 1** (unlike `game_logs`): 0 means "no log
  here", since every row from before the column has no replay to disclaim.

## campaigns

The only stored part of the weekly campaign, and it has to be stored: which
configs existed and which seeds were free are facts about the moment it was
made, the "?" nodes are random rolls, and the layout comes from the game's own
`mapgen`, which the board cannot run. Who holds what is a replay of the week's
scores in time order, done by the page, as crowns are derived. See
[`campaign.md`](campaign.md), "Crowns, the weekly campaign and
embargoes".

`campaign_games` matches a node to its game by seed and jsonb equality, which is
equal by value, never through `sc_config_key`, whose digest of the text tells
the worker's `0.0` from a browser's `0` and so misses nearly every node.

## gauntlets

Gauntlets built on the board's gauntlet page, one row each. The design is in
[`gauntlet.md`](gauntlet.md), "Gauntlets made on the board".

- **Function-only, like `game_logs`.** A row is up to 64 KiB of free text, so
  `/api/gauntlet` holds the only insert, with a size limit, a rate limit and
  `gauntlet-build.mjs`'s `checkBody` in front of it.
- **Never updated but for `listed`.** The id is a hash of the body, so the same
  gauntlet saved twice is one row (`ignore-duplicates`) and an edit is a new
  row. `listed` is the one column `tools/admin.py` changes.
- **Read by id through the function; `listed` rows also with the publishable
  key.** Knowing an id is the permission to read it, as for a play-by-post
  match, so the public read policy is `using (listed)` and nothing else.
- **No progress and no identity.** `by_name` is self-declared display text, as a
  score's is. Who cleared which stage stays in each player's own bests.

## public_replays and public_watchable_replays

`public_replays` puts the consent boundary in SQL rather than in a function's
`if`: a replay is public exactly when a posted score points at it. Posting a
score is a deliberate public act; a game that uploaded itself because *Share
replays* was on is not.

It is the one view deliberately **not** `security_invoker`. The others are
invoker views so a tightened policy still binds their callers; this one exists
to lend out a subset of a table nobody may read, which only owner rights can
do.

The embargo joins through `scores.game_key`, not `game_logs.game_key`. A log
is stamped with whatever digest the game held at upload, which is not
necessarily the key the site filed the map under (`findTwin` in `submit.mjs`
can fold it onto an older one). A score's `game_key` is guaranteed by its
foreign key to name a real `games` row. It is a left join so a log whose key
has no `games` row yet reads as "no embargo" rather than vanishing.

One upload per match: the log the match's *first* score was checked on
(`score_checks.log_id`). Before any check, it is the longest uploaded before
that score was posted, else the longest. The first score, not any verified
one, because a later poster can upload a real game of their own under the same
public `match_id`, post a score for it, and have that verify too. Uploads from
before the post come first because the game uploads as Post opens the form, so
the player's own log is there before a `match_id` is public. Without that, a
bogus longer upload took over the Watch link until the worker's next run.

`public_watchable_replays` exists because `leaderboard/netlify/functions/replay.mjs` reads
exactly one relation on purpose: what may be served is decided in SQL, and the
function has no `if` that could drift from that. Its two halves are gated by
unrelated rules, which is why it is a `union all` rather than a widened view: a
human replay is public because a posted score points at it, and a bot's
because `bot_scores` already is (read by anyone, keyed by no person). It is
granted to `service_role` only, since nothing in it is beyond what the public
can already read another way.

## config_summary

`agg` counts and dates across the group. `rep` takes the setup's own fields
(`settings_json`, `mode`, `players`, `nodes`) from the most recently active
game with `distinct on`, not an aggregate: those columns have no meaningful sum
or max, and every game in the group carries an equivalent value apart from the
seed. Averaging each map's best turns, rather than taking the group's best,
stops one lucky seed standing for the whole setup.

## counted_scores and the crowns

Crowns reward taking first place from somebody else on a contested map, not
volume, which a count of wins or maps would pay out for grinding easy setups.
`counted_scores` counts a score once its replay is `verified`, `outdated` or
not. A score that names a replay and is still unchecked or `missing` counts for
its first 24 hours. A score posted before `sc_legacy_cutoff()` (2026-10-15)
counts under the rule it was posted under. Only the first score posted for a
`match_id` counts, and only the first for a game (`game_digest`).
`game_summary`'s best reads it too: a score that can't hold a crown isn't a
map's record either, so a map can have scores and no best. See
[`campaign.md`](campaign.md), "Crowns, the weekly
campaign and embargoes".

It used to count everything but a `mismatch`, so that a score whose one upload
failed could still count. But `scores` takes inserts from anyone, so a single
request with no replay behind it took a crown and a campaign node. What the
change costs: a score whose upload failed counts only if the player posts again
from the finished game, which uploads again. When it changed, 3 of the 195
scores carrying a `match_id` had no log.

- **Only `verified` counts, `outdated` or not.** `outdated` says the score
  isn't comparable with today's, not that it isn't real. `mismatch` and
  `unreadable` never count, `outdated` or not: a log claiming an older
  `rules_version` that fails to reproduce reads as an `outdated` mismatch, so
  that pairing is forgeable.
- **Old scores keep the rule they were posted under.** Everything posted before
  the cutoff counts unless it is a `mismatch` under current rules, as before.
  The early scores predate uploads, or were played on code that has moved, and
  this change shouldn't wipe them. A cutoff in the view rather than a verdict
  written onto each row: the stored verdicts stay what the replays showed, and
  re-checking can't overwrite it. On the live board it moved no crown.
- **24 hours** is the worker's slack. The cron is hourly, but runs landed 5 to
  10 hours apart, and 2 of 10 failed. A crown or campaign move taken in that
  window disappears if the score never verifies.
- **First post per match**, because a verified `match_id` is public and a copy
  under a second name inherited its check. A clause in the view rather than a
  unique index: nothing to migrate, and a player posting twice is harmless.
- **First post per game**, by `score_checks.game_digest`, because a public
  replay can be uploaded again under a fresh `match_id`. That copy is the
  first post of its "match" and verifies on its own, so the `match_id` rule
  alone doesn't catch it. Two honest games pass through the same boards only
  if every order in them is the same, so an identical game counts once.

## The grant trap

`service_role` bypassing RLS skips policies only. The GRANT system underneath
still applies, so every relation a worker or function touches needs its own
grant, including views and tables read only to decide what work is left. A
missing grant does not degrade: it fails the query outright, and only for the
worker, so the same rows stay visible to the anon key on the site, which is how
it hides.

A comment in `schema.sql` stated this from the first time it caught someone,
and it happened twice more. The mechanism was never the hard part; re-deriving
*which* relations a caller reads, every time one gains a query, was, and a
comment cannot check a list. `tests/test_schema_grants.py` does: it reads the
callers (`tools/*.py`, `leaderboard/netlify/functions/*.mjs`) and the grants and requires
them to agree.

The third time was a view chain. Owner rights stop at the first view that sets
`security_invoker`: Postgres checks what *that* view reads against the role
running the query, however deep it sits. `public_watchable_replays` and
`public_replays` run with owner rights, but `game_embargoes` then reads
`campaign_games` as `service_role`. With no grant there, every Watch link failed
with "permission denied for view campaign_games" from the day the embargo join
went in until the grant was added. The test now walks view chains
(`test_a_role_can_read_what_its_invoker_views_read`).

The same applies to the public: `game_summary` is `security_invoker`, so a
missing grant on `configs` or `config_tag_counts` fails the whole home page
query, not just its config name.

## Play-by-post tables

A different kind of thing from the rest of the board, kept apart on purpose.
The board's tables are append-only, carry no identity, and describe finished
single-player results. A play-by-post match is live, it advances, and a seat
has to be something only one person can submit for. Rather than weaken the
guarantees the rest of the schema makes, multiplayer gets its own two tables
with their own rules. The design is in [`pbp.md`](pbp.md).

- **The server holds no board.** The board is a pure function of the settings,
  the seed and every turn's orders and dice, which `replay.reconstruct` already
  rebuilds. So the tables store the inputs and each client rebuilds the
  position, which is also why a finished match is an ordinary log that resumes,
  reviews and verifies like any other.
- **Seat tokens are the weakest kind of identity.** Random per match per seat,
  stored only as a hash, meaningful in one match. They are not accounts, they
  do not group a person's games, and there is still no client id on the board.
- **Function-only, like `game_logs`.** That is what makes a size limit, a rate
  limit and a token check enforceable.
- **`pbp_matches.log` is the one genuine UPDATE** on the board, rewritten each
  time a turn resolves. It is in `GameLog.encoded` form so a finished match
  needs no conversion to be watched, resumed or verified.
- **`seats` is the full roster**, a bot seat simply absent, so an unclaimed seat
  was representable before the lobby existed. An open seat is a missing token
  hash rather than a second field to keep in step with it.
- **The deadline lives in the table.** `GameLog` stamps `created_at` and
  `updated_at` but never reads them for logic, so wall-clock time has nowhere
  else to live. A null `deadline_hours` is a real choice for two people who
  know each other.
- **`pbp_orders` is unique per (match, turn, seat)**, so a resubmission is a
  conflict the function decides rather than an accident. The only column ever
  written afterwards is `board_digest`. Orders are stored as submitted and
  judged by `engine.apply_order` for every seat alike, as for a bot on the wire.
  There is no owner field: the seat is stamped from the token, so a foreign
  order cannot be expressed at all.
- **`board_digest` is a coherence tripwire, not anti-cheat.** Honest clients
  agree by construction; the digest catches a stale build or a real
  nondeterminism bug. A client that would lie about it would lie about its
  orders too.
- **`source` discloses** a held seat or a bot's move, the posture `hand` takes
  on a posted score.
- **`title`, `names` and `winner` are self-declared display text**, never read
  for logic. `winner` is trusted exactly as far as `finished` beside it.
