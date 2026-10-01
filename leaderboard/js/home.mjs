import { configured, eq, insert, select, selectPage, UNIQUE_VIOLATION } from "./api.mjs";
import { campaignMark } from "./campaign.mjs";
import { GAME_URL } from "./config.mjs";
import { weekParam, weekStart } from "./crowns.mjs";
import { deflate } from "./deflate-browser.mjs";
import {
  botChips, botLeadBadge, campaignBadge, clear, configBadge, el, embargoBadge, embargoNote, fogBadge,
  leaderCredit, mapSummary, relativeTime, showError,
} from "./format.mjs";
import {
  filtersFromParams, isFiltered, listQuery, MODES, PLAYER_COUNTS, SORTS, urlFor, viewKind,
} from "./listing.mjs";
import { mountMyScores, myName } from "./me.mjs";
import { mountNav } from "./nav.mjs";
import { configTitle } from "./setup.mjs";
import { compareScores } from "./standings.mjs";
import { encodeToken, newSeedSetup } from "./token-encode.mjs";

const target = document.getElementById("games");
const groupTarget = document.getElementById("group-toggle");
const filterTarget = document.getElementById("filters");
const headTarget = document.getElementById("config-head");

// How many cards one press of "Show more" adds (and the first load shows).
const PAGE_SIZE = 50;

/**
 * The "Show more" control under the list. Each press fetches the next page and
 * appends it. A card already on screen is skipped rather than drawn twice:
 * the list is ordered by last activity, so a score posted since the last page
 * moves its map to the top and pushes everything below it down one row.
 * Offsets count rows fetched, not rows drawn, which keeps the paging lined up
 * with the server. A failed page leaves the list alone and lets you retry.
 * `keep` drops a row the server couldn't (see unplayedBy).
 */
function showMore(list, { kind, query }, render, offset, keep) {
  const keyOf = (r) => r[kind === "config" ? "config_key" : "game_key"];
  const seen = new Set(list.map(keyOf));
  const button = el("button", { class: "mini", type: "button", text: "Show more" });
  const status = el("span", { class: "name-status" });
  const wrap = el("div", { class: "show-more" }, [button, status]);

  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "";
    try {
      const page = await selectPage(query, { offset, size: PAGE_SIZE });
      offset += page.rows.length;
      const fresh = page.rows.filter((r) => !seen.has(keyOf(r)) && seen.add(keyOf(r)) && keep(r));
      wrap.before(...fresh.map(render));
      if (page.more) button.disabled = false;
      else wrap.remove();
    } catch (err) {
      button.disabled = false;
      status.textContent = err.message;
    }
  });
  return wrap;
}

/** By-game vs. by-config, styled like game.mjs's turns/lost ranking toggle.
 * Hidden once already narrowed to one config — there is nothing left to
 * group there. Every other filter rides along. */
function groupToggle(filters) {
  if (filters.config) return null;
  const linkFor = (value, label) => el("a", {
    class: filters.group === value ? "btn" : "btn ghost",
    href: urlFor({ ...filters, group: value }),
    text: label,
  });
  return el("div", { class: "sorts" }, [linkFor("game", "By game"), linkFor("config", "By config")]);
}

function picker(name, label, value, options) {
  const select = el("select", { name, "aria-label": label },
    options.map(([optValue, optLabel]) => {
      const option = el("option", { value: optValue, text: optLabel });
      if (String(optValue) === String(value)) option.selected = true;
      return option;
    }));
  return el("label", { class: "pick" }, [el("span", { text: label }), select]);
}

function toggle(name, label, on, title) {
  const box = el("input", { type: "checkbox", name, value: "1" });
  box.checked = on;
  return el("label", { class: "toggle", title }, [box, label]);
}

/**
 * Search, sort and the filters, as one GET form whose fields are the URL's own
 * params — so a change just navigates to the view it names, and the back
 * button, a bookmark and a pasted link all work with no state of our own. A
 * select or a checkbox applies the moment it changes; the search box on Enter
 * or its button. The bot and config filters have no field of their own (a
 * card's chip or badge is the way in), so they ride along as hidden inputs.
 */
function controls(filters) {
  const kind = viewKind(filters);
  const form = el("form", { class: "list-controls", method: "get", action: "index.html", role: "search" });
  for (const name of ["config", "bot", "group"]) {
    if (filters[name] && !(name === "group" && filters.group === "game")) {
      form.append(el("input", { type: "hidden", name, value: filters[name] }));
    }
  }
  const search = el("input", {
    type: "text", name: "q", value: filters.q, maxlength: "40", "aria-label": "Search",
    placeholder: kind === "game" ? "Setup, tag, leader or seed" : "Setup name or tag",
  });
  form.append(el("div", { class: "search-row" }, [
    search, el("button", { class: "mini", type: "submit", text: "Search" }),
  ]));

  const picks = el("div", { class: "picks" }, [
    picker("sort", "Sort", filters.sort, SORTS[kind].map((s) => [s.value, s.label])),
    picker("players", "Players", filters.players || "",
      [["", "Any"], ...PLAYER_COUNTS.map((n) => [n, `${n}`])]),
    picker("mode", "Map", filters.mode, [["", "Any"], ...MODES.map((m) => [m, m[0].toUpperCase() + m.slice(1)])]),
    picker("fog", "Fog", filters.fog, [["", "Any"], ["on", "On"], ["off", "Off"]]),
  ]);
  form.append(picks);

  // The per-map filters: a config has no single record to be contested or
  // led, so the grouped view leaves them out (listing.mjs drops them too).
  if (kind === "game") {
    const toggles = [
      toggle("contested", "Contested", filters.contested,
        "Maps two or more players have a counted score on — the ones a crown can be held on"),
      toggle("botlead", "Bot leads", filters.botlead, "Maps where no human score beats the best bot yet"),
    ];
    if (myName()) {
      toggles.push(toggle("unplayed", "Unplayed by me", filters.unplayed,
        `Maps ${myName()} has no score on yet`));
    }
    form.append(el("div", { class: "toggles" }, toggles));
  }

  const go = () => location.assign(urlFor(filtersFromParams(new URLSearchParams(new FormData(form)))));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    go();
  });
  form.addEventListener("change", (event) => {
    if (event.target !== search) go();
  });
  return form;
}

/** What the controls can't show: which bot is filtered on, if any, and a way
 * out of every filter at once — keeping the grouping and the sort. */
function filterBar(filters) {
  if (!isFiltered(filters)) return null;
  const children = [];
  if (filters.bot) children.push(el("span", { class: "current", text: `Vs ${filters.bot}` }));
  const reset = urlFor({ group: filters.config ? "game" : filters.group, sort: filters.sort });
  children.push(el("a", { class: "clear", href: reset, text: "Clear filters" }));
  return el("div", { class: "filters" }, children);
}

/**
 * Every map this browser's player has a score on, keyed by game_key, with
 * their best result there — newest map first, so `listQuery`'s capped
 * `not.in` excludes the ones most likely to be on the first page. Empty with
 * no remembered name, and quiet on failure: a card without the marker is
 * still the card.
 */
async function myMaps() {
  const name = myName();
  if (!name) return new Map();
  const rows = await select(
    `scores?select=game_key,turns,lost,users!inner(name_key)` +
      `&users.name_key=${eq(name.toLowerCase())}&order=submitted_at.desc&limit=2000`,
  ).catch(() => []);
  const best = new Map();
  for (const r of rows) {
    const held = best.get(r.game_key);
    if (!held || compareScores(r, held) < 0) best.set(r.game_key, { turns: r.turns, lost: r.lost });
  }
  return best;
}

/** "You: 25" on a map this browser's player has a score on. */
function mineBadge(score) {
  if (!score) return null;
  return el("span", {
    class: "badge mine",
    title: `Your best here: ${score.turns} turns · ${score.lost} lost`,
    text: `You: ${score.turns}`,
  });
}

/**
 * Post a name for a config nobody has named yet. First post wins —
 * configs.config_key is the primary key and there's no UPDATE policy — so a
 * race here just means someone beat you to it, reported the same way
 * ensureUser() in submit.mjs reports a race on a user's name.
 *
 * Tags used to travel with this form too; they don't any more. A tag is now
 * gated on having actually posted a score (`config_tags.score_id`), so it's
 * entered on submit.html alongside the score itself instead — see
 * `submitTags` there. This form stays name-only, still ungated, still
 * first-wins, exactly as before.
 */
async function nameConfig(configKey, name) {
  const row = { config_key: configKey, name: name.trim() };
  const by = myName();
  if (by) row.by_name = by;
  await insert("configs", row);
}

function nameForm(configKey) {
  const nameField = el("input", { type: "text", maxlength: "40", "aria-label": "Name this setup", placeholder: "Name this setup" });
  const status = el("span", { class: "name-status" });
  const button = el("button", { class: "mini", type: "submit", text: "Name it" });
  const form = el("form", { class: "compare" }, [nameField, button, status]);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!nameField.value.trim()) {
      status.textContent = "Add a name first.";
      return;
    }
    button.disabled = true;
    status.textContent = "";
    try {
      await nameConfig(configKey, nameField.value);
      location.reload();
    } catch (err) {
      button.disabled = false;
      if (err.code === UNIQUE_VIOLATION) {
        status.textContent = "Someone just named this setup — reloading…";
        setTimeout(() => location.reload(), 900);
      } else if (err.code === "23514") {
        status.textContent = "That name is too long.";
      } else {
        status.textContent = err.message;
      }
    }
  });
  return form;
}

/**
 * The way back into the game from a config's page: this setup with no seed on
 * it, so the game rolls a fresh map from it (`Settings.seed is None` ->
 * `resolve_seed` rolls one at start, and the menu's seed field reads "random").
 *
 * The counterpart to game.mjs's "Play this map", which is the same door onto the
 * other side of the split this page is about: that one pins the seed and carries
 * the leader's score as a target, because a map has a board to beat. A config
 * has no single score to hand over — its games are different maps of unequal
 * difficulty — so this link carries the setup alone.
 *
 * Returns null rather than throwing on a setup it can't encode: this is an
 * offer, and load()'s catch puts an error message where the game list goes. A
 * button we couldn't build is a button that isn't there.
 */
async function newSeedLink(game) {
  if (!GAME_URL) return null;
  let token;
  try {
    token = await encodeToken(newSeedSetup(game.settings_json), deflate);
  } catch {
    return null;
  }
  return el("a", {
    class: "btn play",
    href: `${GAME_URL}#${token}`,
    title: "Open the game on this setup, with a freshly rolled map",
    text: "Play a new seed",
  });
}

/** The header strip shown once the list is filtered to one config: its title
 * and the way to play it, its tags (now sourced from `config_tag_counts` via
 * `game_summary`/`config_summary`'s `config_tags` column — ranked by how many
 * distinct players entered each one when posting a score, not entered here),
 * and — while it has no name yet — the form to give it one. */
async function configHead(game) {
  const title = el("h2", { class: "config-title", text: configTitle(game) });
  const play = await newSeedLink(game);
  const children = [el("div", { class: "config-line" }, play ? [title, play] : [title])];
  const tags = game.config_tags || [];
  if (tags.length) {
    children.push(el("p", { class: "config-tags", text: tags.map((t) => `#${t}`).join(" ") }));
  }
  const wrap = el("div", { class: "config-head" }, children);
  if (!game.config_name) wrap.append(nameForm(game.config_key));
  return wrap;
}

/**
 * This week's campaign nodes that have a map on the board, by game_key. Quiet
 * on failure: a board without the campaign views just shows no badge.
 */
async function campaignMarks() {
  const thisWeek = weekStart(new Date());
  const rows = await select(
    `campaign_games?select=game_key,week_start,node_id,kind&week_start=${eq(weekParam(thisWeek))}`,
  ).catch(() => []);
  return new Map(rows.map((entry) => [entry.game_key, campaignMark(entry, thisWeek)]));
}

function row(game, campaigns, mine) {
  const holder = leaderCredit(game);
  const scoreCount = `${game.score_count} ${game.score_count === 1 ? "score" : "scores"}`;
  // While embargoed, the card keeps the leader and the turn count next to
  // them (the headline figure below) — a target to chase — but drops every
  // other detail this line would otherwise carry: lost/hand say more about
  // *how* a score was made than the bare turn count does, and last activity
  // is exactly the kind of "who's trying, and when" signal an embargo is
  // meant to keep from the rest of the group. See game.mjs's renderEmbargoed
  // for the same cut on the map's own page.
  const detail = embargoNote(game.embargo_until)
    ? scoreCount
    : [
        `${game.best_lost} lost`,
        game.best_hand < game.best_turns ? `${game.best_hand} by hand` : null,
        scoreCount,
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
    el("div", { class: "card-tags" }, [
      configBadge(game), fogBadge(game), mineBadge(mine.get(game.game_key)),
      campaignBadge(campaigns.get(game.game_key)), botLeadBadge(game), embargoBadge(game),
      ...botChips(game),
    ]),
  ]);
}

/** A config-grouped row: no single score to headline (its games may be
 * different seeds of unequal difficulty), so the card states the setup and
 * how much has been played on it, and drills into the per-game list on click
 * — the same place a config badge already goes. System count is a config-wide
 * fact (every game in the group shares the same node count, since `nodes` is
 * part of what `sc_config_key` groups by), unlike an embargo, which is set per
 * map and would be misleading to show at this level — see game.mjs/row()
 * above for where that belongs instead. */
function configRow(config) {
  const detail = [
    `${config.nodes} ${config.nodes === 1 ? "system" : "systems"}`,
    `${config.game_count} ${config.game_count === 1 ? "map" : "maps"}`,
    `${config.score_count} ${config.score_count === 1 ? "score" : "scores"}`,
    relativeTime(config.last_activity),
  ].join(" · ");

  const body = el("a", { class: "card-body", href: `index.html?config=${encodeURIComponent(config.config_key)}` }, [
    el("div", { class: "card-main" }, [
      el("div", { class: "setup", text: configTitle(config) }),
      el("div", { class: "card-meta", text: detail }),
    ]),
  ]);

  return el("div", { class: "card" }, [
    body, el("div", { class: "card-tags" }, [fogBadge(config, "config"), ...botChips(config)]),
  ]);
}

async function load() {
  mountNav();
  mountMyScores();
  if (!configured()) {
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  const filters = filtersFromParams(new URLSearchParams(location.search));
  try {
    // Your own maps are only on the critical path when they shape the query.
    const pending = myMaps();
    const played = filters.unplayed ? [...(await pending).keys()] : [];
    const list = listQuery(filters, { played });
    const { kind } = list;
    const [{ rows: fetched, more }, campaigns, mine] = await Promise.all([
      selectPage(list.query, { size: PAGE_SIZE }), campaignMarks(), pending,
    ]);
    // listQuery excludes only the first MAX_EXCLUDED of a player's maps by key;
    // anything past that is dropped here as it arrives.
    const keep = (r) => !(kind === "game" && filters.unplayed && mine.has(r.game_key));
    const rows = fetched.filter(keep);
    target.classList.remove("loading");

    clear(groupTarget);
    const groups = groupToggle(filters);
    if (groups) groupTarget.append(groups);

    clear(filterTarget).append(controls(filters));
    const bar = filterBar(filters);
    if (bar) filterTarget.append(bar);

    clear(headTarget);
    if (filters.config && kind === "game" && rows.length) {
      headTarget.append(await configHead(rows[0]));
    }

    const render = (r) => (kind === "config" ? configRow(r) : row(r, campaigns, mine));
    if (!rows.length && !more) {
      clear(target).append(
        el("p", { class: "empty" }, isFiltered(filters)
          ? ["No maps match this filter. ", el("a", { href: urlFor({ group: filters.group }), text: "Clear filters" }), "."]
          : ["No scores posted yet. ", el("a", { href: "submit.html", text: "Be the first" }), "."]),
      );
      return;
    }
    clear(target).append(...rows.map(render));
    if (more) target.append(showMore(fetched, list, render, fetched.length, keep));
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
