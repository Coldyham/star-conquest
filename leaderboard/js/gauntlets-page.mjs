// The public gauntlets page: every listed gauntlet, newest first, each opening
// in the game by its `#g=<id>` link. Only a listed row is readable with the
// publishable key (schema.sql), so this is a plain select; listing itself is
// an admin's call at the author's request (tools/admin.py list-gauntlet).

import { configured, selectPage } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { clear, el, relativeTime, showError } from "./format.mjs";
import { gameLink, listingOf } from "./gauntlet-build.mjs";
import { mountMyScores } from "./me.mjs";
import { mountNav } from "./nav.mjs";

const target = document.getElementById("gauntlets");

// How many cards one press of "Show more" adds (and the first load shows).
const PAGE_SIZE = 30;
const QUERY = "gauntlets?select=gauntlet_id,by_name,created_at,name:body->>name,stages:body->stages" +
  "&listed=is.true&order=created_at.desc,gauntlet_id.desc";

function card(row) {
  const g = listingOf(row);
  const count = `${g.titles.length} ${g.titles.length === 1 ? "stage" : "stages"}`;
  const meta = [count, g.by ? `by ${g.by}` : null, g.created ? relativeTime(g.created) : null];
  return el("div", { class: "card" }, [
    el("a", { class: "card-body", href: gameLink(GAME_URL, g.id) }, [
      el("div", { class: "card-main" }, [
        el("div", { class: "config-title", text: g.name }),
        el("div", { class: "card-meta", text: meta.filter(Boolean).join(" · ") }),
        el("div", { class: "setup", text: g.titles.join(" → ") }),
      ]),
    ]),
    el("div", { class: "card-tags" }, [
      el("a", { class: "badge", href: gameLink(GAME_URL, g.id), text: "Play" }),
      el("a", { class: "badge", href: `gauntlet.html#${g.id}`, text: "Remix" }),
    ]),
  ]);
}

/** "Show more" under the list: fetches the next page and appends it. */
function showMore(offset) {
  const button = el("button", { class: "mini", type: "button", text: "Show more" });
  const status = el("span", { class: "name-status" });
  const wrap = el("div", { class: "show-more" }, [button, status]);
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "";
    try {
      const page = await selectPage(QUERY, { offset, size: PAGE_SIZE });
      offset += page.rows.length;
      wrap.before(...page.rows.map(card));
      if (page.more) button.disabled = false;
      else wrap.remove();
    } catch (err) {
      button.disabled = false;
      status.textContent = err.message;
    }
  });
  return wrap;
}

async function load() {
  mountNav();
  mountMyScores();
  if (!configured()) {
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  try {
    const { rows, more } = await selectPage(QUERY, { size: PAGE_SIZE });
    target.classList.remove("loading");
    if (!rows.length) {
      clear(target).append(el("p", { class: "empty" }, [
        "No gauntlets are listed yet. ",
        el("a", { href: "gauntlet.html", text: "Make one" }),
        " and ask for it to be listed.",
      ]));
      return;
    }
    clear(target).append(...rows.map(card));
    if (more) target.append(showMore(rows.length));
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
