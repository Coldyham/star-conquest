// The Streaks panel on a player card (user.html): the player's running daily
// streaks, each kind counted as daily.html and puzzle.html count them
// (daily.mjs's playerStreaks). Optional, like the Badges panel: a read that
// fails leaves the panel hidden and never touches the rest of the card.

import { eq, inList, select } from "./api.mjs";
import { dayParam, playerStreaks } from "./daily.mjs";
import { clear, el } from "./format.mjs";

// game_keys per query, so each stays one short URL (as badges-card.mjs).
const CHUNK = 80;

const KIND_ROWS = [
  { kind: "puzzle", title: "Daily puzzle", href: "puzzle.html" },
  { kind: "board", title: "Daily board", href: "daily.html" },
];

const days = (n) => `${n} ${n === 1 ? "day" : "days"}`;

/** Draw one player's running streaks into `box`, or leave it hidden. */
export async function drawStreaks(box, nameKey) {
  let found;
  try {
    const users = await select(`users?select=id&name_key=${eq(nameKey)}`);
    if (!users.length) return;
    const scores = await select(`counted_scores?select=game_key,hand,submitted_at&user_id=eq.${users[0].id}&limit=5000`);
    const keys = [...new Set(scores.map((s) => s.game_key))];
    const pages = [];
    for (let i = 0; i < keys.length; i += CHUNK) {
      pages.push(select(`daily_games?select=day,game_key,kind&game_key=${inList(keys.slice(i, i + CHUNK))}`));
    }
    found = playerStreaks(scores, (await Promise.all(pages)).flat(), dayParam(Date.now()));
  } catch {
    return;
  }
  clear(box).append(
    el("h2", { class: "section-title", text: "Daily streaks" }),
    el("div", { class: "tally" }, KIND_ROWS.map(({ kind, title, href }) => {
      const run = found[kind];
      return el("div", { class: "tally-row" }, [
        el("a", { class: "tally-name", href, text: title }),
        el("span", run.days
          ? {
            class: "tally-figs",
            // As on the daily page: the mended count, the true one on hover.
            title: run.onTheDay === run.days
              ? `True streak: every one of the ${days(run.days)} won on the day itself`
              : `True streak: ${days(run.onTheDay)} won on the day itself; the rest won later from the archive`,
            text: run.onTheDay === run.days ? days(run.days) : `${days(run.days)} (${run.onTheDay} true)`,
          }
          : { class: "tally-figs", text: "No streak running" }),
      ]);
    })),
  );
  box.hidden = false;
}
