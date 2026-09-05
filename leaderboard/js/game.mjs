import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import {
  botChips, botProfile, botSummary, clear, competitionRanks, configBadge, credit, el,
  mapSummary, ordinal, relativeTime, scoreSummary, shortTime, showError, userHref,
} from "./format.mjs";
import { mountMyScores } from "./me.mjs";
import { bestBot, botOrder, displayOrder, humanVsBots } from "./standings.mjs";

const heading = document.getElementById("setup");
const tagsTarget = document.getElementById("setup-tags");
const subtitle = document.getElementById("subtitle");
const sortTarget = document.getElementById("sort");
const target = document.getElementById("scores");
const botsSection = document.getElementById("bots");
const botsLede = document.getElementById("bots-lede");
const botsTarget = document.getElementById("bots-table");
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

/**
 * One bot's row, sharing the .score grid with the human table above so the two
 * read as one board. A win takes its place among the bots that won; a loss shows
 * a dash, because botOrder() deliberately doesn't rank the failures against each
 * other (see standings.mjs).
 */
function botRow(row, rank) {
  const classes = ["score", "bot"];
  if (row.won && rank <= 3) classes.push(`rank-${rank}`);
  if (!row.won) classes.push("bot-lost");
  return el("li", { class: classes.join(" ") }, [
    el("span", { class: "rank", text: row.won ? ordinal(rank) : "—" }),
    el("span", { class: "who" }, [
      // A strategy name comes from a models/*.py filename, so it goes in as text
      // and links to the main list filtered to that bot — the same chip target
      // the map heading already uses.
      // The label carries the profile ("knower · search depth 12") but the link
      // still filters on the bare strategy name, which is what index.html and
      // game_summary.bots key on.
      el("a", { class: "nm", href: `index.html?bot=${encodeURIComponent(row.bot)}`, text: botProfile(row) }),
      el("span", { class: "dots", "aria-hidden": "true" }),
    ]),
    el("span", { class: "result", text: botSummary(row) }),
    // Disclosed rather than hidden: a replay that blew its per-decision budget
    // forfeited those turns' orders, so its result depended on how fast the
    // runner was and is not reproducible the way every other row is.
    row.bot_timeouts
      ? el("span", { class: "when warn", title: `${row.bot_timeouts} decisions timed out — not reproducible`, text: "!" })
      : el("span", { class: "when", title: relativeTime(row.computed_at), text: shortTime(row.computed_at) }),
  ]);
}

/**
 * The one sentence above the bot table: whether anyone has actually outplayed
 * the best machine answer to this map. That is the question worth asking of a
 * high-score board — not where a person places among six bots.
 */
function botVerdict(rows, best) {
  const top = bestBot(rows);
  if (!top) return "No bot has taken this map at all.";
  const summary = `${botProfile(top)} — ${botSummary(top)}`;
  switch (humanVsBots(best, rows)) {
    case "ahead": return `The board's best beats every bot. Best of them: ${summary}.`;
    case "tied":  return `The board's best exactly matches the leading bot: ${summary}.`;
    case "behind": return `No one has beaten the leading bot yet: ${summary}.`;
    default: return `Best of them: ${summary}.`;   // nothing posted to compare
  }
}

/**
 * Fill in the bot section, or leave it hidden.
 *
 * Hidden rather than "not computed yet": a board whose owner has never set the
 * worker's secrets would otherwise carry a permanent apology on every map.
 */
function renderBots(rows, best) {
  if (!rows.length) return;
  const ordered = botOrder(rows);
  // Placings are over the winners alone, so they must be looked up per row
  // rather than by position in `ordered` — that only lines up because botOrder
  // happens to put the winners first, and would silently mis-rank if it stopped.
  const winners = ordered.filter((row) => row.won);
  const ranks = new Map(competitionRanks(winners).map((rank, i) => [winners[i].bot, rank]));
  botsLede.textContent =
    `Each bot replayed from the player's seat on this exact map — same seed, ` +
    `same opponents. ${botVerdict(rows, best)}`;
  clear(botsTarget).append(
    el("ol", { class: "scores" }, ordered.map((row) => botRow(row, ranks.get(row.bot)))),
  );
  botsSection.hidden = false;
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
    const [games, scores, bots] = await Promise.all([
      select(`game_summary?select=*&game_key=${eq(gameKey)}&limit=1`),
      select(
        `scores?select=turns,lost,hand,by_name,submitted_at,raw_token,users(name)` +
          `&game_key=${eq(gameKey)}&order=turns.asc,lost.asc,submitted_at.asc`,
      ),
      // The one query allowed to fail quietly. A board running an older
      // schema.sql has no bot_scores table, and PostgREST answers 404 — which
      // inside Promise.all would reject the whole batch and take the human score
      // table down with it. The bot section is an extra; the board is not.
      select(
        `bot_scores?select=bot,won,turns,lost,bot_timeouts,aux,aux_label,computed_at` +
          `&game_key=${eq(gameKey)}`,
      ).catch(() => []),
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

    // After the human table, and off the same fetch: the bots are context for
    // the board above, not a board of their own. `scores[0]` is the turns-leader
    // whatever ranking is on screen, which is the one the verdict compares.
    renderBots(bots, scores.length ? scores[0] : null);
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
