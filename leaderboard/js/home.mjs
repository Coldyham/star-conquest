import { configured, select } from "./api.mjs";
import { clear, el, mapSummary, relativeTime, showError } from "./format.mjs";

const target = document.getElementById("games");

function row(game) {
  const holder = (game.best_user_name || "").trim() || "anonymous";
  const detail = [
    `${game.best_lost} lost`,
    game.best_hand < game.best_turns ? `${game.best_hand} by hand` : null,
    `${game.score_count} ${game.score_count === 1 ? "score" : "scores"}`,
    relativeTime(game.last_activity),
  ].filter(Boolean).join(" · ");

  return el("a", { class: "card", href: `game.html?key=${encodeURIComponent(game.game_key)}` }, [
    el("div", { class: "card-main" }, [
      el("div", { class: "setup", text: mapSummary(game) }),
      el("div", { class: "credit", text: holder }),
      el("div", { class: "card-meta", text: detail }),
    ]),
    // The headline figure, cabinet-style: the score alone, big.
    el("div", { class: "card-score" }, [
      el("strong", { text: `${game.best_turns}` }),
      el("span", { class: "card-score-label", text: "turns" }),
    ]),
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
