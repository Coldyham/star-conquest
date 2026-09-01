import { configured, eq, insert, select, UNIQUE_VIOLATION } from "./api.mjs";
import { mapSummary, scoreSummary } from "./format.mjs";
import { inflate } from "./inflate-browser.mjs";
import { decodeToken } from "./token-decode.mjs";

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

linkField.addEventListener("input", async () => {
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
});

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
    location.href = `game.html?key=${encodeURIComponent(decoded.gameKey)}`;
  } catch (err) {
    button.disabled = false;
    say(err.message, "error");
  }
});
