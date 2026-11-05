// Posting a score: the board writes behind both ways in.
//
// The submit page calls `postScore` once its form is filled in. The game's quick
// post calls `quickPost` directly: the game and the board share an origin, so the
// game loads this module from /board/js/ and posts without opening a tab
// (share.quick_post). One implementation, so the two can never file a score
// differently.

import { configured, eq, insert, rpc, RLS_VIOLATION, select, UNIQUE_VIOLATION } from "./api.mjs";
import { storedSession } from "./auth.mjs";
import { CLAIMED_MESSAGE, nameBlocked } from "./claims.mjs";
import { inflate } from "./inflate-browser.mjs";
import { normalizeTags } from "./tags.mjs";
import { decodeToken, setupIdentity } from "./token-decode.mjs";

/**
 * A row already on the board describing this exact map under some *other* key.
 *
 * `game_key` is a checksum the game stamps over the whole setup, so a field
 * joining Settings moves it for every map ever posted: the same board, replayed
 * after such a change, arrives here under a key the board has never seen and
 * would start a second, empty map page beside the one holding its history.
 * KEY_ALIASES catches the splits someone has already run into and reported;
 * this catches the rest, including the next schema change, by comparing what
 * the map *is* rather than what it once hashed to (see setupIdentity).
 *
 * It also catches two the alias table structurally cannot. A digest can move
 * without any field being added — `challenge_keys` changed how it treats a seat
 * beyond `players` (settings.py, 2026-09-03), which no legacy drop can express,
 * so those links stamp a key *no* build recomputes. And a player on a stale
 * cached build posts under the older digest, which is a split that arrives with
 * nobody having changed anything at all.
 *
 * The four indexed columns narrow it to a handful of rows before the setup is
 * compared — the same seed and player count is already almost an identity, and
 * the limit is a bound on the query, not on the search: rows past it can only
 * be maps that share all four and differ in their knobs.
 */
async function findTwin(decoded) {
  const rows = await select(
    `games?select=game_key,settings_json&mode=${eq(decoded.mode)}&players=${eq(decoded.players)}` +
      `&nodes=${eq(decoded.nodes)}&seed=${eq(decoded.seed)}` +
      `&order=first_seen_at.desc,game_key.asc&limit=50`,
  );
  const want = setupIdentity(decoded.setup);
  // Newest first: where a map is already split across two keys, that is the one
  // a current build stamps and the one fold-game-key.sql merges *into*, so the
  // site and the repair script agree on which link a map ends up under instead
  // of pulling it two ways.
  const twin = rows.find((row) => setupIdentity(row.settings_json) === want);
  return twin ? twin.game_key : null;
}

/**
 * The key this map is already on the board under, or null if it is new here.
 *
 * The stamped key wins whenever the board already holds it — that is the map
 * page every link out of this site points at. Only when it is unknown do we look
 * for the same map under an older key.
 *
 * Exported because the submit page's preview needs the same answer *before*
 * anything is written: whether to offer an embargo at all. One implementation,
 * so the field can never be offered for a map the submit path will then refuse
 * to embargo.
 */
export async function lookupGame(decoded) {
  const found = await select(`games?select=game_key&game_key=${eq(decoded.gameKey)}&limit=1`);
  if (found.length) return decoded.gameKey;
  return findTwin(decoded);
}

/**
 * The key to file this score (or bare setup) under: find or create the game
 * row, and say whether an embargo actually took effect.
 *
 * Select-then-insert rather than an upsert: an upsert compiles to ON CONFLICT DO
 * UPDATE, and there is deliberately no UPDATE policy for it to use (schema.sql).
 * A unique violation here just means someone else inserted it a moment ago, which
 * is the outcome we wanted anyway.
 *
 * `embargoUntil` (an ISO timestamp, or null for none) only ever takes effect on
 * the `insert` below — the one moment this map's row does not already exist.
 * `games` is append-only like everything else here, so an embargo requested
 * against a map already on the board is silently ignored rather than
 * retroactively hiding replays someone may already have watched; the returned
 * `embargoed` flag is how the submit page tells the difference.
 */
export async function ensureGame(decoded, embargoUntil) {
  const known = await lookupGame(decoded);
  if (known) return { gameKey: known, embargoed: false };
  const row = {
    game_key: decoded.gameKey,
    mode: decoded.mode,
    players: decoded.players,
    nodes: decoded.nodes,
    seed: decoded.seed,
    settings_json: decoded.setup,
  };
  if (embargoUntil) row.embargo_until = embargoUntil;
  try {
    await insert("games", row);
  } catch (err) {
    if (err.code !== UNIQUE_VIOLATION) throw err;
    return { gameKey: decoded.gameKey, embargoed: false };  // someone else just inserted it
  }
  return { gameKey: decoded.gameKey, embargoed: Boolean(embargoUntil) };
}

/** The name the signed-in caller owns, or "" — signed out, or owning none. */
export async function ownName() {
  if (!storedSession()) return "";
  try {
    return (await rpc("my_name", {})) || "";
  } catch {
    return "";
  }
}

/**
 * The signed-in caller's par on `day`'s puzzle (schema.sql's
 * `sc_my_puzzle_par`): `{turns, lost, status, turns_only, my_turns, my_lost}`
 * when their own best posted score there matches or beats it, else null —
 * signed out, short of it, no par, or on failure. Never anyone else's.
 */
export async function ownPar(day) {
  if (!storedSession()) return null;
  try {
    const par = await rpc("sc_my_puzzle_par", { p_day: day });
    return par && typeof par === "object" ? par : null;
  } catch {
    return null;
  }
}

/**
 * Find or create the user, keyed by name — same race handling as above. A name
 * someone else has claimed is refused here, before the score insert that the
 * database would refuse anyway (schema.sql's `sc_may_use_user`), so the player
 * is told why.
 */
async function ensureUser(name) {
  const query = `users?select=id,name,claimed&name_key=${eq(name.trim().toLowerCase())}&limit=1`;
  const found = await select(query);
  if (found.length) {
    if (nameBlocked(found[0], await ownName())) throw new Error(CLAIMED_MESSAGE);
    return found[0].id;
  }
  try {
    const [created] = await insert("users", { name: name.trim() }, { returning: true, columns: "id" });
    return created.id;
  } catch (err) {
    if (err.code !== UNIQUE_VIOLATION) throw err;
    const [existing] = await select(query);
    if (nameBlocked(existing, await ownName())) throw new Error(CLAIMED_MESSAGE);
    return existing.id;
  }
}

/**
 * The config a setup belongs to, computed by the database rather than here:
 * `sc_config_key` hashes Postgres's own `jsonb::text` cast of `settings_json`,
 * which nothing in JS reproduces byte-for-byte (see `api.mjs`'s `rpc` doc).
 * Called with `decoded.setup` directly — already in hand, no extra `select`
 * needed to fetch it back off whatever `games` row `ensureGame` resolved to.
 */
async function configKeyFor(setup) {
  return rpc("sc_config_key", { settings: setup });
}

/**
 * Attach this submission's tags to the config it was scored on. Entirely
 * best-effort: caught here rather than left to the caller, so a rejected tag
 * (or a board whose schema.sql hasn't been re-pasted yet, and so has no
 * `config_tags` table at all — this JS auto-deploys independently of that
 * manual SQL step) can never turn a successfully posted score into an error
 * the player sees. `ignoreDuplicates` against the (score_id, tag_key)
 * constraint means a retried submission doesn't fail the whole batch over
 * tags it already added.
 */
async function submitTags(setup, scoreId, userId, tagsRaw) {
  const tags = normalizeTags(tagsRaw);
  if (!tags.length) return;
  try {
    const configKey = await configKeyFor(setup);
    const rows = tags.map((tag) => ({ config_key: configKey, score_id: scoreId, user_id: userId, tag }));
    await insert("config_tags", rows, { onConflict: "score_id,tag_key", ignoreDuplicates: true });
  } catch {
    // Tags are a nicety on top of a posted score, never load-bearing for it.
  }
}

/**
 * Post a decoded challenge link's score under `name`. Answers the map's key and
 * whether a requested embargo took effect (`ensureGame`).
 */
export async function postScore(decoded, name, { embargoUntil = null, tags = "" } = {}) {
  const { gameKey, embargoed } = await ensureGame(decoded, embargoUntil);
  const userId = await ensureUser(name);
  const [score] = await insert("scores", {
    game_key: gameKey,
    user_id: userId,
    turns: decoded.challenge.turns,
    lost: decoded.challenge.lost,
    hand: decoded.challenge.hand,
    by_name: decoded.challenge.by,
    // Names the uploaded replay behind this score, for tools/verify_scores.py.
    // The game posts the log straight to game_logs, so this side never sees it
    // — only the id, and the two can arrive in either order.
    match_id: decoded.challenge.log,
    // The campaign's answer when this game was started, for its grace.
    campaign_start: decoded.challenge.campaign,
    raw_token: decoded.token,
  }, { returning: true }).catch((err) => {
    // Claimed between ensureUser's check and this insert: sc_may_use_user refuses it.
    throw err.code === RLS_VIOLATION ? new Error(CLAIMED_MESSAGE) : err;
  });
  // Best-effort and never blocking: see submitTags's own doc comment.
  await submitTags(decoded.setup, score.id, userId, tags);
  return { gameKey, embargoed };
}

/**
 * The game's quick post: post `token`'s score under `name`, then answer what an
 * embargoed map page shows anyone — how many scores the map has and who holds
 * the best — plus the map's key for the game's *View on board*.
 *
 * Only the aggregate, never the score list, so the answer reads the same on an
 * embargoed map as on an open one (game.mjs's renderEmbargoed). No embargo and
 * no tags: a player who wants either uses the submit page.
 */
export async function quickPost(token, name) {
  if (!configured()) throw new Error("The leaderboard is not connected to its database yet.");
  if (!name || !name.trim()) throw new Error("No name to post under.");
  const decoded = await decodeToken(token, inflate);
  if (!decoded.challenge) throw new Error("That link carries no score.");
  if (decoded.seed === null) throw new Error("That link has no fixed map.");
  const { gameKey } = await postScore(decoded, name);
  // Quiet once the score is in: a failure past this point would send the game
  // back to the entry form to post it a second time.
  const [summary] = await select(
    `game_summary?select=score_count,best_turns,best_lost,best_user_name,best_holders` +
      `&game_key=${eq(gameKey)}&limit=1`,
  ).catch(() => []);
  return { game_key: gameKey, ...(summary || {}) };
}
