import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import {
  botChips, clear, competitionRanks, configBadge, credit, el, mapSummary, ordinal,
  relativeTime, scoreSummary, shortTime, showError, userHref,
} from "./format.mjs";
import { mountMyScores } from "./me.mjs";
import { displayOrder } from "./standings.mjs";

const heading = document.getElementById("setup");
const tagsTarget = document.getElementById("setup-tags");
const subtitle = document.getElementById("subtitle");
const sortTarget = document.getElementById("sort");
const target = document.getElementById("scores");
const params = new URLSearchParams(location.search);
const gameKey = params.get("key") || "";
const sortKey = params.get("sort") === "lost" ? "lost" : "turns";

/**
 * The name cell: a link to that player's card, unless the score is credited to a
 * link's `by` field rather than to a poster, which names nobody to look up.
 */
function nameCell(score) {
  const posted = ((score.users && score.users.name) || "").trim();
  return posted
    ? el("a", { class: "nm", href: userHref([posted]), text: posted })
    : el("span", { class: "nm", text: credit(score) });
}

function scoreRow(score, rank) {
  return el("li", { class: rank <= 3 ? `score rank-${rank}` : "score" }, [
    el("span", { class: "rank", text: ordinal(rank) }),
    // The dot leader is its own flexible element rather than trailing dots on the
    // name: a name long enough to wrap used to drag the dots into the middle of it.
    el("span", { class: "who" }, [
      nameCell(score),
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

/** Toggle between the board's two rankings, each a plain link so the choice
 * stays shareable. Highlights the active one with the same .btn/.btn.ghost
 * pair the rest of the site uses for "current vs. not". */
function sortToggle() {
  const linkFor = (value, label) => el("a", {
    class: value === sortKey ? "btn" : "btn ghost",
    href: `game.html?key=${encodeURIComponent(gameKey)}&sort=${value}`,
    text: label,
  });
  return el("div", { class: "sorts" }, [linkFor("turns", "Fewest turns"), linkFor("lost", "Fewest lost")]);
}

async function load() {
  mountMyScores();
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
      select(`game_summary?select=*&game_key=${eq(gameKey)}&limit=1`),
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
    clear(tagsTarget).append(configBadge(game), ...botChips(game));
    subtitle.textContent = scores.length
      ? `${scores.length} ${scores.length === 1 ? "score" : "scores"} posted · first seen ${relativeTime(game.first_seen_at)}`
      : "No scores posted yet.";
    clear(sortTarget).append(sortToggle());

    // The server order above (turns then lost then earliest submission) is
    // exactly how the turns-leader is found, regardless of which ranking is
    // on screen — "Play this map" always hands back that target, since the
    // game itself compares turns first.
    const link = playLink(scores.length ? scores[0].raw_token : null);

    const ranked = displayOrder(scores, sortKey);
    const ranks = competitionRanks(ranked);
    clear(target).append(
      el("ol", { class: "scores" }, ranked.map((s, i) => scoreRow(s, ranks[i]))),
      ...(link ? [link] : []),
    );
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
