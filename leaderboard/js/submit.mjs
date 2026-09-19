import { configured, eq, insert, rpc, select, UNIQUE_VIOLATION } from "./api.mjs";
import { mapSummary, scoreSummary } from "./format.mjs";
import { inflate } from "./inflate-browser.mjs";
// Posting again from the same browser shouldn't mean retyping your name — the
// whole point of arriving here from the game's win screen is one click. The same
// remembered name is what puts "My scores" in the marquee.
import { mountMyScores, myName, rememberName } from "./me.mjs";
import { normalizeTags } from "./tags.mjs";
import { decodeToken, fragmentOf, setupIdentity } from "./token-decode.mjs";

const form = document.getElementById("form");
const linkField = document.getElementById("link");
const nameField = document.getElementById("name");
const tagsField = document.getElementById("tags");
const embargoField = document.getElementById("embargo");
const scoreFields = document.getElementById("score-fields");
const scoreHint = document.getElementById("score-hint");
const tagsHint = document.getElementById("tags-hint");
const preview = document.getElementById("preview");
const status = document.getElementById("status");
const button = document.getElementById("go");

function say(message, kind = "") {
  status.textContent = message;
  status.className = `status ${kind}`;
}

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
 * The key to file this score (or bare setup) under: find or create the game
 * row, and say whether an embargo actually took effect.
 *
 * Select-then-insert rather than an upsert: an upsert compiles to ON CONFLICT DO
 * UPDATE, and there is deliberately no UPDATE policy for it to use (schema.sql).
 * A unique violation here just means someone else inserted it a moment ago, which
 * is the outcome we wanted anyway.
 *
 * The stamped key wins whenever the board already holds it — that is the map
 * page every link out of this site points at. Only when it is unknown do we look
 * for the same map under an older key.
 *
 * `embargoUntil` (an ISO timestamp, or null for none) only ever takes effect on
 * the `insert` below — the one moment this map's row does not already exist.
 * `games` is append-only like everything else here, so an embargo requested
 * against a map already on the board is silently ignored rather than
 * retroactively hiding replays someone may already have watched; the returned
 * `embargoed` flag is how the submit handler tells the difference.
 */
async function ensureGame(decoded, embargoUntil) {
  const found = await select(`games?select=game_key&game_key=${eq(decoded.gameKey)}&limit=1`);
  if (found.length) return { gameKey: decoded.gameKey, embargoed: false };
  const twin = await findTwin(decoded);
  if (twin) return { gameKey: twin, embargoed: false };
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

/**
 * The embargo field as an ISO timestamp, or null for "no embargo requested" —
 * the shape `ensureGame` above wants. Clamped to what `games_embargo_bounds`
 * (schema.sql) actually allows, so a stray value here fails as "no embargo"
 * rather than as a rejected insert the player has no way to explain.
 */
function embargoUntilFrom(daysValue) {
  const days = Number(daysValue);
  if (!Number.isFinite(days) || days <= 0) return null;
  const clamped = Math.min(90, Math.max(1, Math.round(days)));
  return new Date(Date.now() + clamped * 86400000).toISOString();
}

/** Find or create the user, keyed by name alone — same race handling as above. */
async function ensureUser(name) {
  const query = `users?select=id&name_key=${eq(name.trim().toLowerCase())}&limit=1`;
  const found = await select(query);
  if (found.length) return found[0].id;
  try {
    const [created] = await insert("users", { name: name.trim() }, { returning: true });
    return created.id;
  } catch (err) {
    if (err.code !== UNIQUE_VIOLATION) throw err;
    const [existing] = await select(query);
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

async function decodePasted() {
  return decodeToken(linkField.value, inflate);
}

/**
 * Switch the form between its two shapes: posting a score (a challenge link)
 * or sharing a bare setup (a plain settings link, or one that never got played
 * to a result — decodeToken's `challenge: null`, token-decode.mjs). The name
 * and tags fields belong to a *score*; a bare setup has neither a poster to
 * credit nor anything a tag could describe (config_tags.score_id requires one
 * to exist), so they hide rather than sitting there unused. The embargo field
 * and its hint stay up in both shapes — see ensureGame's doc comment for why
 * it's harmless to offer even when it may end up doing nothing.
 */
function setSharing(sharing) {
  scoreFields.hidden = sharing;
  scoreHint.hidden = sharing;
  tagsHint.hidden = sharing;
  nameField.required = !sharing;
  button.textContent = sharing ? "Share setup" : "Enter";
}

async function refreshPreview() {
  preview.hidden = true;
  if (!linkField.value.trim()) {
    setSharing(false);
    return;
  }
  try {
    const decoded = await decodePasted();
    setSharing(!decoded.challenge);
    preview.textContent = decoded.challenge
      ? `${mapSummary(decoded)} — ${scoreSummary(decoded.challenge)}`
      : `${mapSummary(decoded)} — no score on this link, just the setup`;
    preview.hidden = false;
    say("");
  } catch {
    // Stay quiet while they are still pasting; submitting is what reports.
  }
}

linkField.addEventListener("input", refreshPreview);

/**
 * Arrive from the game already loaded: submit.html#<token> fills the link in.
 *
 * The token rides in the fragment, not a query string, for the same two reasons
 * the game uses one — it never reaches a server or a Referer header, and it means
 * the game can hand us its existing link shape verbatim. fragmentOf already takes
 * everything after the '#', so a whole pasted URL works here too.
 */
async function prefill() {
  const name = myName();
  if (name) nameField.value = name;

  const token = fragmentOf(location.hash);
  if (!token) {
    linkField.focus();
    return;
  }
  linkField.value = token;
  await refreshPreview();
  // The link is the part that was tedious; put the cursor on what's left. A
  // bare setup has no name field to land on even with none remembered — the
  // button is the only control left either way.
  (name || scoreFields.hidden ? button : nameField).focus();
}

mountMyScores();
prefill();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!configured()) {
    say("This leaderboard isn't connected to its database yet — see leaderboard/README.md.", "error");
    return;
  }

  let decoded;
  try {
    decoded = await decodePasted();
  } catch {
    say("That doesn't look like a Star Conquest link.", "error");
    return;
  }

  const sharing = !decoded.challenge;
  if (!sharing && !nameField.value.trim()) {
    say("Add a name to post under.", "error");
    return;
  }
  if (decoded.seed === null) {
    // A plain settings-share link may leave the seed to be rolled fresh at
    // start (Settings.seed is None) — fine for opening the game, but there is
    // no single map to register or score without one. Told apart from "that
    // doesn't look like a link at all" above, since this one decoded fine.
    say("This link has no fixed map (its seed is set to “random”) — pick a seed in the game's Advanced menu and share that link instead.", "error");
    return;
  }

  button.disabled = true;
  say(sharing ? "Sharing…" : "Posting…");
  try {
    const embargoUntil = embargoUntilFrom(embargoField.value);
    const { gameKey, embargoed } = await ensureGame(decoded, embargoUntil);

    if (sharing) {
      location.href = `game.html?key=${encodeURIComponent(gameKey)}`;
      return;
    }

    const userId = await ensureUser(nameField.value);
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
      raw_token: decoded.token,
    }, { returning: true });
    rememberName(nameField.value);
    // Best-effort and never blocking: see submitTags's own doc comment.
    await submitTags(decoded.setup, score.id, userId, tagsField.value);
    if (embargoUntil && !embargoed) {
      // The embargo field was filled in, but this map was already on the
      // board (from an earlier score, or someone else's bare share) — say so
      // rather than silently posting as if it had been honoured.
      say("Posted — but this map was already on the board, so the embargo you set wasn't applied. Redirecting…");
      await new Promise((resolve) => setTimeout(resolve, 1600));
    }
    location.href = `game.html?key=${encodeURIComponent(gameKey)}`;
  } catch (err) {
    button.disabled = false;
    say(err.message, "error");
  }
});
