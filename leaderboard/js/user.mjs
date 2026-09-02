import { configured, inList, select } from "./api.mjs";
import {
  clear, el, mapSummary, ordinal, relativeTime, scoreSummary, shortTime, showError, userHref,
} from "./format.mjs";
import { mountMyScores, myName } from "./me.mjs";
import { standings, tally } from "./standings.mjs";

// Four names is as many as a cabinet row holds before the score column collapses,
// and past that a "comparison" has stopped being one.
const MAX_ROSTER = 4;
// Enough that a regular's whole record fits on the page, few enough that the two
// follow-up queries stay one short URL each.
const MAX_MAPS = 60;

const heading = document.getElementById("who");
const lede = document.getElementById("lede");
const tallyBox = document.getElementById("tally");
const target = document.getElementById("scores");
const form = document.getElementById("compare");
const nameField = document.getElementById("rival");
const extras = document.getElementById("extras");

/** ?u=Andrew&u=Rival, deduplicated the way the database folds case (name_key). */
function askedNames() {
  const seen = new Map();
  for (const raw of new URLSearchParams(location.search).getAll("u")) {
    const name = raw.trim();
    if (name && !seen.has(name.toLowerCase())) seen.set(name.toLowerCase(), name);
  }
  return [...seen.values()].slice(0, MAX_ROSTER);
}

const asked = askedNames();
const has = (name) => asked.some((other) => other.toLowerCase() === name.toLowerCase());

const go = (names) => location.assign(userHref(names));

// -- chrome ----------------------------------------------------------------

function setHeading(names) {
  const title = names.length ? names.join(" vs ") : "Player card";
  heading.textContent = title;
  document.title = `${title} · Star Conquest Leaderboard`;
}

function mountCompare() {
  nameField.disabled = asked.length >= MAX_ROSTER;
  if (nameField.disabled) {
    nameField.placeholder = `${MAX_ROSTER} players at a time`;
    form.querySelector("button").disabled = true;
  } else if (!asked.length) {
    // The same box is the page's search when nobody has been chosen yet.
    nameField.placeholder = "Player name…";
  }
  const mine = myName();
  if (mine && !has(mine) && !nameField.disabled) {
    const button = el("button", { class: "mini", type: "button", text: `Add my scores (${mine})` });
    button.addEventListener("click", () => go([...asked, mine]));
    extras.append(button);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = nameField.value.trim();
  if (!name) return;
  // Already on the board: nothing to navigate to, so just clear the box.
  if (has(name)) {
    nameField.value = "";
    return;
  }
  go([...asked, name]);
});

// -- the player card -------------------------------------------------------

function figures(player, comparing) {
  const parts = [
    `${player.maps} ${player.maps === 1 ? "map" : "maps"}`,
    `${player.scores} ${player.scores === 1 ? "score" : "scores"}`,
    `${player.records} ${player.records === 1 ? "record" : "records"}`,
  ];
  if (comparing) parts.push(`leads ${player.leads}`);
  return parts.join(" · ");
}

function tallyRow(player, comparing) {
  const row = el("div", { class: "tally-row" }, [
    el("a", { class: "tally-name", href: userHref([player.name]), text: player.name }),
    el("span", { class: "tally-figs", text: figures(player, comparing) }),
  ]);
  if (comparing) {
    // Dropping one name is the other half of adding one; without it the only way
    // back out of a comparison is editing the URL by hand.
    const drop = el("button", {
      class: "drop", type: "button", "aria-label": `Remove ${player.name}`, text: "×",
    });
    // Match on the folded key, not the display name: the card shows the spelling
    // its owner registered, which need not be the one in this page's URL.
    drop.addEventListener("click", () => go(asked.filter((name) => name.toLowerCase() !== player.key)));
    row.append(drop);
  }
  return row;
}

function drawTally(sums, comparing) {
  const note = comparing
    ? `${sums.contested} contested ${sums.contested === 1 ? "map" : "maps"}` +
      (sums.draws ? ` · ${sums.draws} drawn` : "")
    : null;
  clear(tallyBox).append(
    el("div", { class: "tally" }, [
      ...sums.players.map((player) => tallyRow(player, comparing)),
      note ? el("p", { class: "tally-note", text: note }) : null,
    ]),
  );
}

// -- the map list ----------------------------------------------------------

function playerLine(entry, comparing) {
  if (!entry.score) {
    return el("li", { class: "score absent" }, [
      el("span", { class: "rank", text: "—" }),
      el("span", { class: "who" }, [
        el("span", { class: "nm", text: entry.name }),
        el("span", { class: "dots", "aria-hidden": "true" }),
      ]),
      el("span", { class: "result", text: "not played" }),
    ]);
  }

  const classes = ["score"];
  if (entry.rank && entry.rank <= 3) classes.push(`rank-${entry.rank}`);
  if (comparing && entry.lead) classes.push("lead");

  return el("li", { class: classes.join(" ") }, [
    el("span", { class: "rank", text: entry.rank ? ordinal(entry.rank) : "" }),
    el("span", { class: "who" }, [
      el("span", { class: "nm", text: entry.name }),
      // Only the best attempt is placed, so say when it was picked out of several.
      entry.tries > 1 ? el("span", { class: "tries", text: `×${entry.tries}` }) : null,
      el("span", { class: "dots", "aria-hidden": "true" }),
    ]),
    el("span", { class: "result", text: scoreSummary(entry.score) }),
    el("span", {
      class: "when",
      title: relativeTime(entry.score.at),
      text: shortTime(entry.score.at),
    }),
  ]);
}

function mapEntry(row, game, comparing, shown) {
  const field = `${row.fieldSize} ${row.fieldSize === 1 ? "score" : "scores"}`;
  return el("li", { class: "entry" }, [
    el("a", { class: "entry-head", href: `game.html?key=${encodeURIComponent(row.gameKey)}` }, [
      el("span", { class: "setup", text: game ? mapSummary(game) : row.gameKey }),
      el("span", { class: "entry-field", text: `${field} →` }),
    ]),
    el(
      "ul",
      { class: "lines" },
      row.entries.filter((entry) => shown.has(entry.key)).map((entry) => playerLine(entry, comparing)),
    ),
  ]);
}

// -- loading ---------------------------------------------------------------

/** PostgREST's nesting, flattened to what standings.mjs works on. */
const asEntry = (row) => ({
  gameKey: row.game_key,
  playerKey: row.users.name_key,
  turns: row.turns,
  lost: row.lost,
  hand: row.hand,
  at: row.submitted_at,
});

async function load() {
  mountMyScores();
  mountCompare();
  setHeading(asked);

  if (!asked.length) {
    target.classList.remove("loading");
    lede.textContent = "Type a name to see every score they have posted.";
    clear(target).append(
      el("p", { class: "empty" }, [
        "Nobody chosen yet. Pick a name from ",
        el("a", { href: "index.html", text: "any map's board" }),
        ", or type one above.",
      ]),
    );
    nameField.focus();
    return;
  }

  if (!configured()) {
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }

  try {
    // Filtering on the embedded users row (hence !inner) rather than resolving
    // names to ids first: one round trip fewer, and it hands back each name as
    // its owner originally typed it, which is what the page should display.
    const rows = await select(
      `scores?select=game_key,turns,lost,hand,submitted_at,users!inner(name,name_key)` +
        `&users.name_key=${inList(asked.map((name) => name.trim().toLowerCase()))}` +
        `&order=submitted_at.desc&limit=2000`,
    );

    // Keep the typed spelling for anyone who has posted nothing — they still get
    // a row on the card (with a × to drop them) rather than vanishing silently.
    const canonical = new Map(asked.map((name) => [name.toLowerCase(), name]));
    for (const row of rows) canonical.set(row.users.name_key, row.users.name);
    const roster = [...canonical].map(([key, name]) => ({ key, name }));
    setHeading(roster.map((player) => player.name));

    const entries = rows.map(asEntry);
    // rows arrive newest first, so first mention is most-recently-played order.
    const keys = [...new Set(entries.map((entry) => entry.gameKey))].slice(0, MAX_MAPS);
    const wanted = new Set(keys);

    target.classList.remove("loading");
    if (!keys.length) {
      const names = roster.map((player) => player.name).join(" or ");
      clear(target).append(
        el("p", { class: "empty" }, [
          `No scores posted by ${names} yet. `,
          el("a", { href: "submit.html", text: "Post one" }),
          ".",
        ]),
      );
      drawTally(tally([], roster), roster.length > 1);
      return;
    }

    const [games, field] = await Promise.all([
      select(`games?select=*&game_key=${inList(keys)}`),
      // Every score on those maps, which is what turns a result into a placing.
      select(`scores?select=game_key,turns,lost&game_key=${inList(keys)}&limit=5000`),
    ]);

    const setups = new Map(games.map((game) => [game.game_key, game]));
    const board = standings(
      entries.filter((entry) => wanted.has(entry.gameKey)),
      roster,
      field.map((row) => ({ gameKey: row.game_key, turns: row.turns, lost: row.lost })),
    );

    const comparing = roster.length > 1;
    const sums = tally(board, roster);
    drawTally(sums, comparing);
    lede.textContent = comparing
      ? "Best result each has posted per map, contested maps first. The green bar marks who is ahead; the placing is against the whole board."
      : "Every map with a posted score, newest first, and where the best attempt places on that map's board.";

    // A name with nothing on the board would otherwise add a "not played" line to
    // every single map; the card above already reports that they have none.
    const shown = new Set(sums.players.filter((player) => player.maps).map((player) => player.key));
    clear(target).append(
      el("ul", { class: "entries" }, board.map((row) => mapEntry(row, setups.get(row.gameKey), comparing, shown))),
    );
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
