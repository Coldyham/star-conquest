import { configured, select } from "./api.mjs";
import { clear, el, mapSummary, relativeTime, scoreSummary, showError } from "./format.mjs";

const target = document.getElementById("games");

function row(game) {
  const best = el("div", { class: "best" }, [
    el("strong", { text: scoreSummary({ turns: game.best_turns, lost: game.best_lost, hand: game.best_hand }) }),
    el("span", { class: "credit", text: ` by ${(game.best_user_name || "").trim() || "anonymous"}` }),
  ]);
  const meta = `${game.score_count} ${game.score_count === 1 ? "score" : "scores"} · ${relativeTime(game.last_activity)}`;

  return el("a", { class: "card", href: `game.html?key=${encodeURIComponent(game.game_key)}` }, [
    el("div", { class: "card-main" }, [
      el("div", { class: "setup", text: mapSummary(game) }),
      best,
    ]),
    el("div", { class: "card-meta", text: meta }),
  ]);
}

async function load() {
  if (!configured()) {
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  try {
    const games = await select(
      "game_summary?select=*&score_count=gt.0&order=last_activity.desc.nullslast&limit=50",
    );
    target.classList.remove("loading");
    if (!games.length) {
      clear(target).append(
        el("p", { class: "empty" }, [
          "No scores posted yet. ",
          el("a", { href: "submit.html", text: "Be the first" }),
          ".",
        ]),
      );
      return;
    }
    clear(target).append(...games.map(row));
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
