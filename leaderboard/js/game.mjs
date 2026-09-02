import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import {
  clear, competitionRanks, credit, el, mapSummary, ordinal, relativeTime, scoreSummary,
  shortTime, showError,
} from "./format.mjs";

const heading = document.getElementById("setup");
const subtitle = document.getElementById("subtitle");
const target = document.getElementById("scores");
const gameKey = new URLSearchParams(location.search).get("key") || "";

function scoreRow(score, rank) {
  return el("li", { class: rank <= 3 ? `score rank-${rank}` : "score" }, [
    el("span", { class: "rank", text: ordinal(rank) }),
    // The dot leader is its own flexible element rather than trailing dots on the
    // name: a name long enough to wrap used to drag the dots into the middle of it.
    el("span", { class: "who" }, [
      el("span", { class: "nm", text: credit(score) }),
      el("span", { class: "dots", "aria-hidden": "true" }),
    ]),
    el("span", { class: "result", text: scoreSummary(score) }),
    el("span", { class: "when", title: relativeTime(score.submitted_at), text: shortTime(score.submitted_at) }),
  ]);
}

/** A link back into the game, carrying the leader's score as the target to beat. */
function playLink(token) {
  if (!GAME_URL || !token) return null;
  return el("a", { class: "btn play", href: `${GAME_URL}#${token}`, target: "_blank", rel: "noopener", text: "Play this map" });
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

    const ranks = competitionRanks(scores);

    const link = playLink(scores.length ? scores[0].raw_token : null);
    clear(target).append(
      el("ol", { class: "scores" }, scores.map((s, i) => scoreRow(s, ranks[i]))),
      ...(link ? [link] : []),
    );
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
