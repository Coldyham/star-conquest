import { configured } from "./api.mjs";
import { mapSummary, scoreSummary } from "./format.mjs";
import { inflate } from "./inflate-browser.mjs";
// Posting again from the same browser shouldn't mean retyping your name — the
// whole point of arriving here from the game's win screen is one click. The same
// remembered name is what puts "My scores" in the marquee.
import { mountMyScores, myName, rememberName } from "./me.mjs";
import { mountNav } from "./nav.mjs";
import { ensureGame, lookupGame, postScore } from "./post.mjs";
import { decodeToken, fragmentOf } from "./token-decode.mjs";

const form = document.getElementById("form");
const linkField = document.getElementById("link");
const nameField = document.getElementById("name");
const tagsField = document.getElementById("tags");
const embargoField = document.getElementById("embargo");
const embargoFields = document.getElementById("embargo-fields");
const embargoHint = document.getElementById("embargo-hint");
const scoreFields = document.getElementById("score-fields");
const scoreHint = document.getElementById("score-hint");
const preview = document.getElementById("preview");
const status = document.getElementById("status");
const button = document.getElementById("go");

function say(message, kind = "") {
  status.textContent = message;
  status.className = `status ${kind}`;
}

/**
 * The embargo field as an ISO timestamp, or null for "no embargo requested" —
 * the shape post.mjs's `ensureGame` wants. Clamped to what `games_embargo_bounds`
 * (schema.sql) actually allows, so a stray value here fails as "no embargo"
 * rather than as a rejected insert the player has no way to explain.
 */
function embargoUntilFrom(daysValue) {
  const days = Number(daysValue);
  if (!Number.isFinite(days) || days <= 0) return null;
  const clamped = Math.min(90, Math.max(1, Math.round(days)));
  return new Date(Date.now() + clamped * 86400000).toISOString();
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
 * is orthogonal to both shapes — a bare share is one of the two ways a map
 * first reaches the board — so `setEmbargoOffered` drives it separately, off
 * whether this map is new rather than off what is being posted.
 */
function setSharing(sharing) {
  scoreFields.hidden = sharing;
  scoreHint.hidden = sharing;
  nameField.required = !sharing;
  button.textContent = sharing ? "Share setup" : "Enter";
}

/**
 * Offer the embargo only for a map the board has never seen. On one it already
 * holds — which is every arrival from the game's own *Enter on leaderboard* on
 * a map someone has already posted — the field can do nothing but be ignored,
 * so it and its explanation come off the page entirely rather than describing a
 * setting that won't apply.
 *
 * Clearing the field on the way out matters: the submit path still reads it,
 * and a number typed before the lookup landed would otherwise ride along
 * invisibly into `embargoUntilFrom` and raise the "wasn't applied" notice on an
 * embargo nobody could still see they had asked for.
 */
function setEmbargoOffered(offered) {
  embargoFields.hidden = !offered;
  if (!offered) {
    embargoField.value = "";
    syncEmbargoHint();
  }
}

/** The embargo's explanation follows the box: nothing typed, nothing to explain. */
function syncEmbargoHint() {
  embargoHint.hidden = !embargoField.value.trim();
}

embargoField.addEventListener("input", syncEmbargoHint);

/**
 * Is this map already on the board? Decides whether the embargo field is worth
 * offering at all (see setEmbargoOffered).
 *
 * Fails *open*: an unreachable or unconfigured board answers "new", leaving the
 * field up. Being offered an embargo that then turns out not to apply is the
 * mild failure — the submit path already says so — while hiding it on a blip
 * would quietly cost a genuine setter their one chance to use it, on the single
 * submission where it was ever possible.
 */
async function alreadyOnBoard(decoded) {
  if (!configured() || decoded.seed === null) return false;
  try {
    return Boolean(await lookupGame(decoded));
  } catch {
    return false;
  }
}

// Each keystroke starts a fresh pass and both halves of one are async, so a slow
// reply for a half-pasted link could otherwise land after the finished link's and
// leave the form describing the wrong one.
let previewPass = 0;

async function refreshPreview() {
  const pass = ++previewPass;
  preview.hidden = true;
  if (!linkField.value.trim()) {
    setSharing(false);
    setEmbargoOffered(true);
    return;
  }
  try {
    const decoded = await decodePasted();
    if (pass !== previewPass) return;
    setSharing(!decoded.challenge);
    preview.textContent = decoded.challenge
      ? `${mapSummary(decoded)} — ${scoreSummary(decoded.challenge)}`
      : `${mapSummary(decoded)} — no score on this link, just the setup`;
    preview.hidden = false;
    say("");
    // Deliberately not awaited: this is the one part of the pass that goes to
    // the network, and `prefill` waits on this function to decide where to put
    // the cursor. Landing ready to type is worth more than the embargo field
    // resolving a moment sooner, and it only ever resolves *away*.
    void (async () => {
      const known = await alreadyOnBoard(decoded);
      if (pass !== previewPass) return;
      setEmbargoOffered(!known);
    })();
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

mountNav();
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

    if (sharing) {
      const { gameKey } = await ensureGame(decoded, embargoUntil);
      location.href = `game.html?key=${encodeURIComponent(gameKey)}`;
      return;
    }

    const { gameKey, embargoed } = await postScore(decoded, nameField.value, {
      embargoUntil, tags: tagsField.value,
    });
    rememberName(nameField.value);
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
