// The name this browser last posted under.
//
// Nothing here is load-bearing: private mode and blocked site data both throw on
// localStorage, and every caller must work the same without a remembered name.
// It is only ever a convenience — one less thing to retype, and a way into your
// own player page from the marquee.

import { el, userHref } from "./format.mjs";

const NAME_KEY = "sc_leaderboard_name";

export function myName() {
  try {
    return (localStorage.getItem(NAME_KEY) || "").trim();
  } catch {
    return "";
  }
}

export function rememberName(name) {
  try {
    localStorage.setItem(NAME_KEY, name.trim());
  } catch {
    // Fine — remembering the name is a convenience, not part of posting.
  }
}

/**
 * Put a "My scores" button in the marquee, for a browser that has posted before.
 *
 * The four pages all ship the same static header; this is the one part of it that
 * depends on who is looking, so it is added at runtime rather than hardcoded.
 */
export function mountMyScores() {
  const name = myName();
  const actions = document.querySelector(".bar .actions");
  if (!name || !actions) return;
  actions.prepend(el("a", { class: "btn ghost", href: userHref([name]), text: "My scores" }));
}
