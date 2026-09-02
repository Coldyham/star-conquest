import { configured, eq, insert, select, UNIQUE_VIOLATION } from "./api.mjs";
import { mapSummary, scoreSummary } from "./format.mjs";
import { inflate } from "./inflate-browser.mjs";
import { decodeToken, fragmentOf } from "./token-decode.mjs";

// Posting again from the same browser shouldn't mean retyping your name — the
// whole point of arriving here from the game's win screen is one click.
const NAME_KEY = "sc_leaderboard_name";

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
 * Find or create the game row.
 *
 * Select-then-insert rather than an upsert: an upsert compiles to ON CONFLICT DO
 * UPDATE, and there is deliberately no UPDATE policy for it to use (schema.sql).
 * A unique violation here just means someone else inserted it a moment ago, which
 * is the outcome we wanted anyway.
 */
async function ensureGame(decoded) {
  const found = await select(`games?select=game_key&game_key=${eq(decoded.gameKey)}&limit=1`);
  if (found.length) return;
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

const remembered = (key) => {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return ""; // private mode, or site data blocked — never load-bearing
  }
};

/**
 * Arrive from the game already loaded: submit.html#<token> fills the link in.
 *
 * The token rides in the fragment, not a query string, for the same two reasons
 * the game uses one — it never reaches a server or a Referer header, and it means
 * the game can hand us its existing link shape verbatim. fragmentOf already takes
 * everything after the '#', so a whole pasted URL works here too.
 */
function prefill() {
  const name = remembered(NAME_KEY);
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
    await ensureGame(decoded);
    const userId = await ensureUser(nameField.value);
    await insert("scores", {
      game_key: decoded.gameKey,
      user_id: userId,
      turns: decoded.challenge.turns,
      lost: decoded.challenge.lost,
      hand: decoded.challenge.hand,
      by_name: decoded.challenge.by,
      raw_token: decoded.token,
    });
    try {
      localStorage.setItem(NAME_KEY, nameField.value.trim());
    } catch {
      // Fine — remembering the name is a convenience, not part of posting.
    }
    location.href = `game.html?key=${encodeURIComponent(decoded.gameKey)}`;
  } catch (err) {
    button.disabled = false;
    say(err.message, "error");
  }
});
