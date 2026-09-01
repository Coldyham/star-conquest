import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { clear, credit, el, mapSummary, relativeTime, scoreSummary, showError } from "./format.mjs";

const heading = document.getElementById("setup");
const subtitle = document.getElementById("subtitle");
const target = document.getElementById("scores");
const gameKey = new URLSearchParams(location.search).get("key") || "";

function scoreRow(score, rank) {
  return el("li", { class: rank === 1 ? "score leader" : "score" }, [
    el("span", { class: "rank", text: `${rank}` }),
    el("span", { class: "who", text: credit(score) }),
    el("span", { class: "result", text: scoreSummary(score) }),
    el("span", { class: "when", text: relativeTime(score.submitted_at) }),
  ]);
}

/** A link back into the game, carrying the leader's score as the target to beat. */
function playLink(token) {
  if (!GAME_URL || !token) return null;
  return el("a", { class: "btn play", href: `${GAME_URL}#${token}`, text: "Play this map" });
}

async function load() {
  if (!configured()) {
    heading.textContent = "Not connected";
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  if (!gameKey) {
    heading.textContent = "No map chosen";
    showError(target, "That link is missing its map key.");
    return;
  }

  try {
    const [games, scores] = await Promise.all([
      select(`games?select=*&game_key=${eq(gameKey)}&limit=1`),
      select(
        `scores?select=turns,lost,hand,by_name,submitted_at,raw_token,users(name)` +
          `&game_key=${eq(gameKey)}&order=turns.asc,lost.asc,submitted_at.asc`,
      ),
    ]);
    target.classList.remove("loading");

    if (!games.length) {
      heading.textContent = "Unknown map";
      showError(target, "No map on the board has that key.");
      return;
    }
    const game = games[0];
    heading.textContent = mapSummary(game);
    subtitle.textContent = scores.length
      ? `${scores.length} ${scores.length === 1 ? "score" : "scores"} posted · first seen ${relativeTime(game.first_seen_at)}`
      : "No scores posted yet.";

    const link = playLink(scores.length ? scores[0].raw_token : null);
    clear(target).append(
      el("ol", { class: "scores" }, scores.map((s, i) => scoreRow(s, i + 1))),
      ...(link ? [link] : []),
    );
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
