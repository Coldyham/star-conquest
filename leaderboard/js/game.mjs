import { configured, eq, select } from "./api.mjs";
import { CURRENT_RULES_VERSION, GAME_URL } from "./config.mjs";
import { deflate } from "./deflate-browser.mjs";
import {
  botChips, botProfile, botSummary, clear, competitionRanks, configBadge, credit, el,
  embargoNote, leaderCredit, mapSummary, ordinal, relativeTime, scoreSummary, shortTime, showError, userHref,
} from "./format.mjs";
import { mountMyScores } from "./me.mjs";
import { aliasFor } from "./token-decode.mjs";
import { botWatchSetup, encodeToken } from "./token-encode.mjs";
import { bestBot, botOrder, botWatchKind, displayOrder, humanVsBots } from "./standings.mjs";

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

function scoreRow(score, rank, replays) {
  const version = replays.get(score.match_id);
  const watchable = version === CURRENT_RULES_VERSION;
  const outdated = version !== undefined && !watchable;
  return el("li", { class: rank <= 3 ? `score rank-${rank}` : "score" }, [
    el("span", { class: "rank", text: ordinal(rank) }),
    // The dot leader is its own flexible element rather than trailing dots on the
    // name: a name long enough to wrap used to drag the dots into the middle of it.
    el("span", { class: "who" }, [
      nameCell(score),
      el("span", { class: "dots", "aria-hidden": "true" }),
    ]),
    // Watch (or its outdated-replay note) rides *inside* the result cell rather
    // than becoming a fifth column: .score is a four-track grid and a bot row
    // has four children, so a fifth track would leave every bot row carrying an
    // empty column and its gap.
    el("span", { class: "result" }, [
      el("span", { text: scoreSummary(score) }),
      ...(watchable ? [watchLink(score.match_id)] : []),
      ...(outdated ? [outdatedNote()] : []),
    ]),
    el("span", { class: "when", title: relativeTime(score.submitted_at), text: shortTime(score.submitted_at) }),
  ]);
}

/**
 * "Watch" on a score whose replay is actually there.
 *
 * The link goes back into the *game*, not into a player here: the engine is
 * Python and the game already has the whole reviewer — `reconstruct`, the fog
 * replay and the scrubber. Reimplementing any of that in JS would be a second
 * engine to keep in step with the first.
 *
 * Shown only for ids `public_replays` actually returned, so it can never lead to
 * a 404: a score can name a match whose upload never arrived.
 */
function watchLink(matchId) {
  return el("a", {
    class: "watch",
    href: `${GAME_URL}#log=${matchId}`,
    target: "_blank",
    rel: "noopener",
    title: "Replay this game in the browser",
    text: "Watch",
  });
}

/**
 * Stands in for Watch on a score whose replay exists but predates the current
 * rules — disclosed rather than just dropped, so a historic score reads as
 * "can't replay this one" instead of looking indistinguishable from a score
 * that never had a replay at all.
 */
function outdatedNote() {
  return el("span", {
    class: "outdated",
    title: "This replay was recorded under an earlier ruleset and can no longer be reconstructed exactly.",
    text: "Older ruleset",
  });
}

/**
 * The Watch cell for one bot row — 0 or 1 elements to splice into `.result`,
 * decided by `botWatchKind` (standings.mjs, so the decision itself is covered
 * by that module's own tests rather than needing a DOM harness here).
 *
 * A "current" row reuses `watchLink` verbatim: `bot_scores.match_id` names a
 * replay exactly the way `scores.match_id` does (both minted by the same
 * `replay._new_match_id`), and the game's `#log=` path — fetch, decode,
 * `open_history` — doesn't care which table the id came from. "outdated"
 * mirrors the human table's own disclosure (`outdatedNote`) rather than
 * silently falling back to something that may equally have moved on. "legacy"
 * is the one surviving use of the old method: a win computed before its
 * replay was stored at all reconstructs the match live instead, the way every
 * bot's Watch link used to (`botWatchSetup`, token-encode.mjs — mirrors
 * `tools/sim.play_settings` field for field). "none" (a loss) offers nothing.
 */
async function botWatchCell(gameSettings, row) {
  if (!GAME_URL) return [];
  const kind = botWatchKind(row);
  if (kind === "current") return [watchLink(row.match_id)];
  if (kind === "outdated") return [outdatedNote()];
  if (kind === "none") return [];
  // "legacy": nothing was ever uploaded for this one, so reconstruct it — the
  // stored setup, the seed, and `row.bot` standing in for the human's seat,
  // already in autoplay — and let the game's own engine play it out live from
  // turn one, rather than scrubbing a recorded log it doesn't have.
  let token;
  try {
    token = await encodeToken(botWatchSetup(gameSettings, row.bot, row.aux), deflate);
  } catch {
    return [];
  }
  return [el("a", {
    class: "watch",
    href: `${GAME_URL}#${token}`,
    target: "_blank",
    rel: "noopener",
    title: "Replay this bot in the browser",
    text: "Watch",
  })];
}

/**
 * One bot's row, sharing the .score grid with the human table above so the two
 * read as one board. A win takes its place among the bots that won; a loss shows
 * a dash, because botOrder() deliberately doesn't rank the failures against each
 * other (see standings.mjs). `watchCell` is this row's Watch cell (0 or 1
 * elements — see `botWatchCell`), built ahead of time by renderBots since
 * encoding a legacy link is async.
 */
function botRow(row, rank, watchCell) {
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
    el("span", { class: "result" }, [
      el("span", { text: botSummary(row) }),
      ...watchCell,
    ]),
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
async function renderBots(rows, best, gameSettings) {
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
  const watchCells = await Promise.all(ordered.map((row) => botWatchCell(gameSettings, row)));
  clear(botsTarget).append(
    el("ol", { class: "scores" }, ordered.map((row, i) => botRow(row, ranks.get(row.bot), watchCells[i]))),
  );
  botsSection.hidden = false;
}

/** A link back into the game, carrying the leader's score as the target to beat. */
function playLink(token) {
  if (!GAME_URL || !token) return null;
  return el("a", { class: "btn play", href: `${GAME_URL}#${token}`, target: "_blank", rel: "noopener", text: "Play this map" });
}

/**
 * "Play this map" for a map with no scores posted yet — registered here as a
 * bare setup (js/submit.mjs) rather than reached through a win. There is no
 * `raw_token` to reuse (that rides on a score, per `playLink`'s usual caller),
 * so this re-encodes `game.settings_json` instead — it already carries the
 * exact seed the setup was registered under, so whoever opens it plays the
 * same map, not a fresh roll (contrast home.mjs's newSeedLink, which is for a
 * *config* and deliberately blanks the seed).
 */
async function freshPlayLink(game) {
  if (!GAME_URL) return null;
  let token;
  try {
    token = await encodeToken(game.settings_json, deflate);
  } catch {
    return null;
  }
  return playLink(token);
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

/**
 * Every score's replay status, keyed by match_id: the `rules_version` its log
 * was recorded under if one was ever uploaded, absent otherwise. A version
 * equal to `CURRENT_RULES_VERSION` can be watched and reconstructed exactly; an
 * older one can't — the game's own engine has moved past the rules that replay
 * was recorded under, so reconstructing it would show a different game than the
 * one actually played (`GameLog.is_current`, the same check the game itself
 * uses to gate its own Watch/resume). Rather than link to a replay it can no
 * longer show right, the board marks it outdated instead of offering it (see
 * `scoreRow`/`outdatedNote`).
 *
 * Asked by id rather than by map: a score's `game_key` and its log's are stamped
 * by different code paths (the token's `Challenge.key` and `GameLog.setup_key`),
 * and a folded key would make a `game_key` lookup quietly miss.
 *
 * Fails quietly like the bot query does — a board on an older schema.sql has no
 * `public_replays` view (or no `rules_version` column on it yet), and a missing
 * "Watch" link is not worth taking the score table down for. Needs GAME_URL too:
 * without somewhere to send a watcher there is nothing to link to.
 */
async function replayVersions(scores) {
  const ids = [...new Set(scores.map((s) => s.match_id).filter(Boolean))];
  if (!GAME_URL || !ids.length) return new Map();
  const rows = await select(
    `public_replays?select=match_id,rules_version&match_id=in.(${ids.map(encodeURIComponent).join(",")})`,
  ).catch(() => []);
  return new Map(rows.map((r) => [r.match_id, r.rules_version]));
}

/**
 * The map page while its embargo is still live (`games.embargo_until`,
 * schema.sql) — a compromise, not a blackout. The full per-score list is
 * never even requested here, so a competitor cannot see who else has played,
 * when, or how: lost, hand and submission time all say more about *how* a
 * score was made than the bare turn count does, and that "how" is exactly
 * what an embargo exists to keep back. What does show is `game_summary`'s own
 * aggregate — who currently holds the best turn count, and how many scores
 * exist in total — because the embargo is meant to leave something to chase,
 * not nothing at all: without a target, there is no reason to keep trying
 * before the reveal.
 *
 * The bots table is unaffected and fetched the same as always: a bot's game
 * is a pure function of the setup and the code (docs/bot-design.md), already
 * fully public via `bot_scores`, so there is no *person's* strategy in it to
 * protect. Its verdict is built off the same aggregate rather than a fetched
 * score row — `{turns, lost}` is all `humanVsBots`/`bestBot` ever read.
 *
 * The play link is always the setup alone (`freshPlayLink`), never a score's
 * `raw_token` — that token is a full challenge link and would smuggle out the
 * very lost/hand/by fields this view is holding back, sitting right there in
 * the page's own HTML whether or not they're ever rendered as text.
 */
async function renderEmbargoed(game, embargoText, bots) {
  subtitle.textContent = [
    game.score_count
      ? `${game.score_count} ${game.score_count === 1 ? "score" : "scores"} posted`
      : "No scores posted yet.",
    embargoText,
  ].filter(Boolean).join(" · ");
  clear(sortTarget);

  const lede = game.score_count
    ? el("p", { class: "lede" }, [
        "Currently ahead: ",
        el("strong", { text: leaderCredit(game) }),
        ` — ${game.best_turns} turns. `,
        "Every other score, and every replay, stays hidden until the embargo lifts.",
      ])
    : el("p", { class: "lede", text: "No scores yet — be the first, and set the target everyone else has to beat." });

  const link = await freshPlayLink(game);
  clear(target).append(lede, ...(link ? [link] : []));

  const best = game.score_count ? { turns: game.best_turns, lost: game.best_lost } : null;
  await renderBots(bots, best, game.settings_json);
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
    // Scores are deliberately not fetched here — only once the map is known
    // not to be embargoed, below. Fetching the full list up front and simply
    // not rendering it would still hand every score's detail to the page
    // (and anyone watching the network tab) before a single row is drawn.
    const [games, bots] = await Promise.all([
      select(`game_summary?select=*&game_key=${eq(gameKey)}&limit=1`),
      // The one query allowed to fail quietly. A board running an older
      // schema.sql has no bot_scores table, and PostgREST answers 404 — which
      // inside Promise.all would reject the whole batch and take the human score
      // table down with it. The bot section is an extra; the board is not.
      select(
        `bot_scores?select=bot,won,turns,lost,bot_timeouts,aux,aux_label,computed_at,` +
          `match_id,rules_version` +
          `&game_key=${eq(gameKey)}`,
      ).catch(() => []),
    ]);
    target.classList.remove("loading");

    if (!games.length) {
      // A key nobody knows is usually a link from before this map was folded onto
      // its current digest (KEY_ALIASES / fold-game-key.sql). Send it on rather
      // than telling someone their own bookmark is wrong. aliasFor resolves the
      // whole chain, so this is one hop and cannot bounce; if the destination is
      // missing too, it says so there.
      const folded = aliasFor(gameKey);
      if (folded !== gameKey) {
        const onward = new URLSearchParams(location.search);
        onward.set("key", folded);          // keeps ?sort= exactly as it was
        location.replace(`game.html?${onward}`);
        return;
      }
      heading.textContent = "Unknown map";
      showError(target, "No map on the board has that key.");
      return;
    }
    const game = games[0];
    heading.textContent = mapSummary(game);
    clear(tagsTarget).append(configBadge(game), ...botChips(game));

    const embargo = embargoNote(game.embargo_until);
    if (embargo) {
      await renderEmbargoed(game, embargo, bots);
      return;
    }

    const scores = await select(
      `scores?select=turns,lost,hand,by_name,submitted_at,raw_token,match_id,users(name)` +
        `&game_key=${eq(gameKey)}&order=turns.asc,lost.asc,submitted_at.asc`,
    );

    subtitle.textContent = scores.length
      ? `${scores.length} ${scores.length === 1 ? "score" : "scores"} posted · first seen ${relativeTime(game.first_seen_at)}`
      : "No scores posted yet.";
    clear(sortTarget).append(sortToggle());

    // The server order above (turns then lost then earliest submission) is
    // exactly how the turns-leader is found, regardless of which ranking is
    // on screen — "Play this map" always hands back that target, since the
    // game itself compares turns first. A map with no scores yet (registered
    // as a bare setup, never played through to a challenge) has no such token
    // to reuse, so it falls back to the setup itself.
    const link = scores.length ? playLink(scores[0].raw_token) : await freshPlayLink(game);

    const ranked = displayOrder(scores, sortKey);
    const ranks = competitionRanks(ranked);
    const replays = await replayVersions(scores);
    clear(target).append(
      el("ol", { class: "scores" }, ranked.map((s, i) => scoreRow(s, ranks[i], replays))),
      ...(link ? [link] : []),
    );

    // After the human table, and off the same fetch: the bots are context for
    // the board above, not a board of their own. `scores[0]` is the turns-leader
    // whatever ranking is on screen, which is the one the verdict compares.
    await renderBots(bots, scores.length ? scores[0] : null, game.settings_json);
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
