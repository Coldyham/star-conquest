import { configured, eq, insert, select, UNIQUE_VIOLATION } from "./api.mjs";
import { mapSummary, scoreSummary } from "./format.mjs";
import { inflate } from "./inflate-browser.mjs";
// Posting again from the same browser shouldn't mean retyping your name — the
// whole point of arriving here from the game's win screen is one click. The same
// remembered name is what puts "My scores" in the marquee.
import { mountMyScores, myName, rememberName } from "./me.mjs";
import { decodeToken, fragmentOf, setupIdentity } from "./token-decode.mjs";

const form = document.getElementById("form");
const linkField = document.getElementById("link");
const nameField = document.getElementById("name");
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
 * The key to file this score under: find or create the game row.
 *
 * Select-then-insert rather than an upsert: an upsert compiles to ON CONFLICT DO
 * UPDATE, and there is deliberately no UPDATE policy for it to use (schema.sql).
 * A unique violation here just means someone else inserted it a moment ago, which
 * is the outcome we wanted anyway.
 *
 * The stamped key wins whenever the board already holds it — that is the map
 * page every link out of this site points at. Only when it is unknown do we look
 * for the same map under an older key.
 */
async function ensureGame(decoded) {
  const found = await select(`games?select=game_key&game_key=${eq(decoded.gameKey)}&limit=1`);
  if (found.length) return decoded.gameKey;
  const twin = await findTwin(decoded);
  if (twin) return twin;
  try {
    await insert("games", {
      game_key: decoded.gameKey,
      mode: decoded.mode,
      players: decoded.players,
      nodes: decoded.nodes,
      seed: decoded.seed,
      settings_json: decoded.setup,
    });
  } catch (err) {
    if (err.code !== UNIQUE_VIOLATION) throw err;
  }
  return decoded.gameKey;
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

async function decodePasted() {
  return decodeToken(linkField.value, inflate);
}

async function refreshPreview() {
  preview.hidden = true;
  if (!linkField.value.trim()) return;
  try {
    const decoded = await decodePasted();
    preview.textContent = `${mapSummary(decoded)} — ${scoreSummary(decoded.challenge)}`;
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
function prefill() {
  const name = myName();
  if (name) nameField.value = name;

  const token = fragmentOf(location.hash);
  if (!token) {
    linkField.focus();
    return;
  }
  linkField.value = token;
  refreshPreview();
  // The link is the part that was tedious; put the cursor on what's left.
  (name ? document.getElementById("go") : nameField).focus();
}

mountMyScores();
prefill();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!configured()) {
    say("This leaderboard isn't connected to its database yet — see leaderboard/README.md.", "error");
    return;
  }
  if (!nameField.value.trim()) {
    say("Add a name to post under.", "error");
    return;
  }

  let decoded;
  try {
    decoded = await decodePasted();
  } catch (err) {
    say(
      err.message.startsWith("not a challenge link")
        ? "That's a valid Star Conquest link, but it has no score on it — paste the one from Challenge a friend after a win."
        : "That doesn't look like a Star Conquest challenge link.",
      "error",
    );
    return;
  }

  button.disabled = true;
  say("Posting…");
  try {
    const gameKey = await ensureGame(decoded);
    const userId = await ensureUser(nameField.value);
    await insert("scores", {
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
    });
    rememberName(nameField.value);
    location.href = `game.html?key=${encodeURIComponent(gameKey)}`;
  } catch (err) {
    button.disabled = false;
    say(err.message, "error");
  }
});
