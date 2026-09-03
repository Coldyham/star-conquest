import { configured, contains, eq, insert, select, UNIQUE_VIOLATION } from "./api.mjs";
import {
  botChips, clear, configBadge, el, mapSummary, relativeTime, showError,
} from "./format.mjs";
import { mountMyScores, myName } from "./me.mjs";
import { configTitle } from "./setup.mjs";

// Columns spelled out rather than `select=*`: game_summary now carries
// settings_json for the badge label, and that's a real payload increase for a
// 50-row list — worth it, but worth stating rather than growing silently the
// next time a column is appended.
const COLUMNS = [
  "game_key", "mode", "players", "nodes", "seed", "last_activity", "score_count",
  "best_turns", "best_lost", "best_hand", "best_by_name", "best_user_name", "best_holders",
  "settings_json", "config_key", "bots", "config_name", "config_tags",
].join(",");

const target = document.getElementById("games");
const filterTarget = document.getElementById("filters");
const headTarget = document.getElementById("config-head");

/** The filter state this view is showing — the only state home.mjs has, and it
 * lives entirely in the URL so every filtered view is a shareable link. */
function filtersFromUrl() {
  const params = new URLSearchParams(location.search);
  return { config: params.get("config") || "", bot: params.get("bot") || "" };
}

function urlFor(filters) {
  const params = new URLSearchParams();
  if (filters.config) params.set("config", filters.config);
  if (filters.bot) params.set("bot", filters.bot);
  const qs = params.toString();
  return qs ? `index.html?${qs}` : "index.html";
}

async function fetchGames(filters) {
  let query = `game_summary?select=${COLUMNS}&score_count=gt.0&order=last_activity.desc.nullslast&limit=50`;
  if (filters.config) query += `&config_key=${eq(filters.config)}`;
  if (filters.bot) query += `&bots=${contains([filters.bot])}`;
  return select(query);
}

function filterBar(filters, bots) {
  const picker = el("select", { "aria-label": "Filter by bot" }, [
    el("option", { value: "", text: "Any bot" }),
    ...bots.map((b) => el("option", {
      value: b.bot,
      text: `${b.bot} (${b.games})`,
      selected: filters.bot === b.bot ? "" : undefined,
    })),
  ]);
  picker.addEventListener("change", () => {
    location.assign(urlFor({ ...filters, bot: picker.value }));
  });

  const children = [picker];
  if (filters.config || filters.bot) {
    children.push(el("a", { class: "clear", href: "index.html", text: "Clear filters" }));
  }
  return el("div", { class: "filters" }, children);
}

/**
 * Post a name (and optional tags) for a config nobody has named yet. First
 * post wins — configs.config_key is the primary key and there's no UPDATE
 * policy — so a race here just means someone beat you to it, reported the
 * same way ensureUser() in submit.mjs reports a race on a user's name.
 */
async function nameConfig(configKey, name, tagsRaw) {
  const tags = [...new Set(
    tagsRaw.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean),
  )].slice(0, 6);
  const row = { config_key: configKey, name: name.trim() };
  if (tags.length) row.tags = tags;
  const by = myName();
  if (by) row.by_name = by;
  await insert("configs", row);
}

function nameForm(configKey) {
  const nameField = el("input", { type: "text", maxlength: "40", "aria-label": "Name this setup", placeholder: "Name this setup" });
  const tagsField = el("input", { type: "text", "aria-label": "Tags", placeholder: "Tags, comma separated" });
  const status = el("span", { class: "name-status" });
  const button = el("button", { class: "mini", type: "submit", text: "Name it" });
  const form = el("form", { class: "compare" }, [nameField, tagsField, button, status]);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!nameField.value.trim()) {
      status.textContent = "Add a name first.";
      return;
    }
    button.disabled = true;
    status.textContent = "";
    try {
      await nameConfig(configKey, nameField.value, tagsField.value);
      location.reload();
    } catch (err) {
      button.disabled = false;
      if (err.code === UNIQUE_VIOLATION) {
        status.textContent = "Someone just named this setup — reloading…";
        setTimeout(() => location.reload(), 900);
      } else if (err.code === "23514") {
        status.textContent = "That name or tag list is too long.";
      } else {
        status.textContent = err.message;
      }
    }
  });
  return form;
}

/** The header strip shown once the list is filtered to one config: its title,
 * its tags, and — while it has none — the form to give it one. */
function configHead(game) {
  const children = [el("h2", { class: "config-title", text: configTitle(game) })];
  const tags = game.config_tags || [];
  if (tags.length) {
    children.push(el("p", { class: "config-tags", text: tags.map((t) => `#${t}`).join(" ") }));
  }
  const wrap = el("div", { class: "config-head" }, children);
  if (!game.config_name) wrap.append(nameForm(game.config_key));
  return wrap;
}

function row(game) {
  const name = (game.best_user_name || "").trim() || "anonymous";
  // A dead heat on turns *and* lost is a shared record, so credit all of it.
  // best_holders is absent unless the game_summary view is current; treat a
  // missing count as the one leading name.
  const others = Math.max(0, Number(game.best_holders || 1) - 1);
  const holder = others ? `${name} & ${others} other${others === 1 ? "" : "s"}` : name;
  const detail = [
    `${game.best_lost} lost`,
    game.best_hand < game.best_turns ? `${game.best_hand} by hand` : null,
    `${game.score_count} ${game.score_count === 1 ? "score" : "scores"}`,
    relativeTime(game.last_activity),
  ].filter(Boolean).join(" · ");

  const body = el("a", { class: "card-body", href: `game.html?key=${encodeURIComponent(game.game_key)}` }, [
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

  return el("div", { class: "card" }, [
    body,
    el("div", { class: "card-tags" }, [configBadge(game), ...botChips(game)]),
  ]);
}

async function load() {
  mountMyScores();
  if (!configured()) {
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  const filters = filtersFromUrl();
  try {
    // Filtering happens server-side, on purpose: filtering only the visible
    // 50 client-side would silently hide older matches instead.
    const [games, bots] = await Promise.all([
      fetchGames(filters),
      select("bot_roster?select=*&order=games.desc,bot.asc"),
    ]);
    target.classList.remove("loading");
    clear(filterTarget).append(filterBar(filters, bots));
    clear(headTarget);
    if (filters.config && games.length) headTarget.append(configHead(games[0]));

    if (!games.length) {
      const filtered = filters.config || filters.bot;
      clear(target).append(
        el("p", { class: "empty" }, filtered
          ? ["No scores match this filter. ", el("a", { href: "index.html", text: "Clear filters" }), "."]
          : ["No scores posted yet. ", el("a", { href: "submit.html", text: "Be the first" }), "."]),
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
