// The name this browser last posted under.
//
// Nothing here is load-bearing: private mode and blocked site data both throw on
// localStorage, and every caller must work the same without a remembered name.
// It is only ever a convenience — one less thing to retype, and a way into your
// own player page from the marquee.

import { userHref } from "./format.mjs";

// One name for everything on this origin: the name a score is posted under, the
// name a play-by-post seat is claimed under, and the game's own pbp prompt
// (`paths.WEB_PBP_NAME_KEY`, pinned by tests/test_leaderboard_sync.py). The
// board shares the game's origin, so they are one stored value.
export const NAME_KEY = "sc_pbp_name";

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
 * Point the menu's "You" at this browser's own player card, which holds the
 * account panel too (user.html). With no name remembered it opens the card's
 * search, where the panel is also shown. Called by nav.mjs's mountNav.
 */
export function mountYou() {
  const name = myName();
  const you = document.getElementById("nav-you");
  if (name && you) you.setAttribute("href", userHref([name]));
}
