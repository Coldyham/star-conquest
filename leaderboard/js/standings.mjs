// Standings maths for the player page: which of two scores is better, where one
// sits on a map's board, and how a roster of players compares map by map.
//
// No DOM and no fetch in here, so tests/ can exercise it directly — the same
// reason competitionRanks() lives in format.mjs rather than in game.mjs.
//
// Everything works on normalised rows, not PostgREST's shapes: an entry is
// {gameKey, playerKey, turns, lost, hand, at}, a field row {gameKey, turns, lost},
// and a roster member {key, name}. user.mjs does the flattening.

/** Fewest turns, then fewest ships lost — the game's own rule (settings.py). */
export function compareScores(a, b) {
  return a.turns - b.turns || a.lost - b.lost;
}

/**
 * Where a score sits on a whole map's board: one more than however many beat it.
 * Equal results share a place and the next distinct one skips it, which is the
 * standard competition ranking competitionRanks() gives an already-sorted list.
 */
export function rankAmong(field, score) {
  let better = 0;
  for (const other of field) if (compareScores(other, score) < 0) better += 1;
  return better + 1;
}

/**
 * The two ranking rules a map's board can offer: fewest turns (the game's own
 * rule, and just compareScores by another name), or fewest ships lost with
 * turns as its tie-break — the same shape, the other way round.
 */
export function scoreComparator(key) {
  return key === "lost" ? (a, b) => a.lost - b.lost || a.turns - b.turns : compareScores;
}

const time = (iso) => Date.parse(iso) || 0;

/**
 * A map's scores in display order: ranked by `key` ("turns" or "lost"), with
 * the earliest submission first among a dead heat — matching how
 * game_summary already credits the earliest of tied holders as
 * best_user_name. Works on raw score rows ({turns, lost, submitted_at, ...}),
 * not the normalised `entry` shape the rest of this module uses.
 */
export function displayOrder(scores, key) {
  const compare = scoreComparator(key);
  return [...scores].sort((a, b) => compare(a, b) || time(a.submitted_at) - time(b.submitted_at));
}

/**
 * One row per map the roster has played, carrying each member's *best* score on
 * it — repeat attempts are counted but only the best one is placed, since that is
 * the one the board ranks.
 *
 * Contested maps sort first, then by the roster's most recent attempt. With one
 * player that first key is constant, so it reads as plain newest-first; with two
 * it puts the head-to-heads at the top, which is the whole point of comparing.
 *
 * @returns [{gameKey, last, played, fieldSize, entries: [{key, name, score, tries, rank, lead}]}]
 *          entries is parallel to `roster`, with a null score where they haven't played.
 */
export function standings(entries, roster, field = []) {
  const pools = new Map();
  for (const row of field) {
    const pool = pools.get(row.gameKey);
    if (pool) pool.push(row);
    else pools.set(row.gameKey, [row]);
  }

  const maps = new Map();
  for (const entry of entries) {
    let map = maps.get(entry.gameKey);
    if (!map) maps.set(entry.gameKey, (map = { gameKey: entry.gameKey, last: 0, best: new Map() }));
    map.last = Math.max(map.last, time(entry.at));
    const held = map.best.get(entry.playerKey);
    if (!held) map.best.set(entry.playerKey, { score: entry, tries: 1 });
    else {
      held.tries += 1;
      if (compareScores(entry, held.score) < 0) held.score = entry;
    }
  }

  const rows = [...maps.values()].map((map) => {
    const pool = pools.get(map.gameKey) || [];
    const row = roster.map(({ key, name }) => {
      const held = map.best.get(key);
      return held
        ? {
            key,
            name,
            score: held.score,
            tries: held.tries,
            rank: pool.length ? rankAmong(pool, held.score) : null,
            lead: false,
          }
        : { key, name, score: null, tries: 0, rank: null, lead: false };
    });

    // Who is ahead *within the roster*, which is a different question from the
    // rank column: leading a two-player comparison on a map both were beaten on
    // is still worth showing. A tie leads jointly and counts as a draw below.
    const played = row.filter((entry) => entry.score);
    if (played.length > 1) {
      const best = played.reduce((a, b) => (compareScores(b.score, a.score) < 0 ? b : a));
      for (const entry of played) entry.lead = compareScores(entry.score, best.score) === 0;
    }

    return {
      gameKey: map.gameKey,
      last: map.last,
      played: played.length,
      fieldSize: pool.length,
      entries: row,
    };
  });

  rows.sort((a, b) => b.played - a.played || b.last - a.last);
  return rows;
}

/**
 * The player card's figures, over the rows standings() returned.
 *
 * `records` is board-wide (they hold or share the map record); `leads` and
 * `draws` are roster-only, counted over contested maps — the ones at least two
 * of the roster have played, matching the `lead` flag above.
 *
 * @returns {players: [{key, name, maps, scores, records, leads}], contested, draws}
 */
export function tally(rows, roster) {
  const players = roster.map(({ key, name }) => ({ key, name, maps: 0, scores: 0, records: 0, leads: 0 }));
  const index = new Map(players.map((player) => [player.key, player]));
  let contested = 0;
  let draws = 0;

  for (const row of rows) {
    for (const entry of row.entries) {
      if (!entry.score) continue;
      const player = index.get(entry.key);
      player.maps += 1;
      player.scores += entry.tries;
      if (entry.rank === 1) player.records += 1;
    }
    if (row.played < 2) continue;
    contested += 1;
    const leaders = row.entries.filter((entry) => entry.lead);
    if (leaders.length === 1) index.get(leaders[0].key).leads += 1;
    else draws += 1;
  }

  return { players, contested, draws };
}

/**
 * The measured full-roster ladder (docs/bot-design.md "Full roster ladder" in
 * the Python repo, mirrored here since JS can't import it), strongest to
 * weakest. Keep in sync with `starconquest/ai.py`'s `LADDER_ORDER`.
 */
export const LADDER_ORDER = ["knower", "marshal", "thinker", "claudebot", "heuristic", "rusherplus"];

/** Ladder order first, any bot missing from it (a fresh drop-in) alphabetical after. */
function byLadderOrder(a, b) {
  const ia = LADDER_ORDER.indexOf(a.bot);
  const ib = LADDER_ORDER.indexOf(b.bot);
  if (ia === -1 && ib === -1) return a.bot.localeCompare(b.bot);
  if (ia === -1) return 1;
  if (ib === -1) return -1;
  return ia - ib;
}

/**
 * A map's bot replays in display order: the ones that took the board first,
 * ranked by the game's own rule, then the ones that didn't, in ladder order.
 *
 * The split is the point. A bot that never won has a `turns` figure — how long
 * it lasted before it was wiped out or the replay hit its turn cap — and that
 * number is *not* a score. Sorting the failures by it would quietly rank them
 * against each other, and putting them in the same ordering as the winners would
 * rank a quick death above a slow victory. So losses sit after every win, ordered
 * by the roster's measured strength instead — a ranking that claims nothing
 * about *this* map, only about the bots in general.
 *
 * Rows are the shape `bot_scores` stores: {bot, won, turns, lost, bot_timeouts}.
 */
export function botOrder(rows) {
  const won = rows.filter((row) => row.won).sort(compareScores);
  const lost = rows.filter((row) => !row.won).sort(byLadderOrder);
  return [...won, ...lost];
}

/**
 * The best bot result on a map, or null if none of them took the board.
 *
 * What the section header compares a human's score against — "31 turns, by
 * thinker" — so it only ever considers wins, for the same reason botOrder splits
 * them out.
 */
export function bestBot(rows) {
  const won = rows.filter((row) => row.won);
  if (!won.length) return null;
  return won.reduce((best, row) => (compareScores(row, best) < 0 ? row : best));
}

/**
 * How a human score stands against the bots: "ahead", "tied", "behind", or null
 * when there is nothing to compare (no score posted, or no bot won).
 *
 * Deliberately measured against the *best* bot rather than counting how many it
 * beats: the interesting question on a board of high scores is whether a person
 * outplayed the best machine answer to that map, not whether they placed
 * mid-table among six of them.
 */
export function humanVsBots(score, rows) {
  const best = bestBot(rows);
  if (!score || !best) return null;
  const delta = compareScores(score, best);
  return delta < 0 ? "ahead" : delta > 0 ? "behind" : "tied";
}
