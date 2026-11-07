import { mountYou } from "./me.mjs";
import { mountTips } from "./tip.mjs";

// The header's collapsible menu (`<details class="menu">` in every page's own
// static header). A native <details> opens and closes on its own summary with
// no JS at all -- the one thing it won't do is close itself when a click lands
// somewhere else on the page, which reads as broken rather than dismissive
// once the menu is the only way to reach "All maps"/"Campaign"/etc. Every page
// calls this, so it also does the other things every page needs: the app wiring
// below, the tap-to-read tooltips (tip.mjs), the header's Play and the menu's You.
export function mountNav() {
  mountApp();
  mountTips();
  mountPlayLink();
  mountYou();
  const menu = document.querySelector(".bar .menu");
  if (!menu) return;
  document.addEventListener("click", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
}

// The header's Play goes where the page's own play link does when it has
// exactly one (a map's "Play this map", the selected campaign node, the day on
// a daily page), else to the game's menu: none or several means nothing is
// picked. Pages draw that link late and redraw it (a campaign node is picked),
// so the header follows <main> rather than each page setting it.
function mountPlayLink() {
  const play = document.querySelector(".bar .play");
  const main = document.querySelector("main");
  if (!play || !main) return;
  const fallback = play.getAttribute("href");
  const sync = () => {
    const links = main.querySelectorAll("a.btn.play[href]");
    const href = links.length === 1 ? links[0].getAttribute("href") : fallback;
    if (play.getAttribute("href") !== href) play.setAttribute("href", href);
  };
  sync();
  new MutationObserver(sync).observe(main, {
    childList: true, subtree: true, attributes: true, attributeFilter: ["href"],
  });
}

// The board is half of the installable app (the manifest and service worker at
// the site root, `tools/pwa/`). Registering the worker here is what lets a
// browser offer the install from a board page, not only from /play/. Inside
// the installed app, the page also records that the board was the last half
// open, so the root router (`tools/pwa/root.html`) reopens it at the next
// launch. Silent everywhere it can't apply, such as a dev server with no root.
function mountApp() {
  try {
    const standalone = navigator.standalone === true ||
      matchMedia("(display-mode: standalone), (display-mode: fullscreen)").matches;
    if (standalone) localStorage.setItem("sc_app_last", "board");
  } catch {
    // storage blocked: the next launch just opens the game, as it always did
  }
  try {
    navigator.serviceWorker?.register("/sw.js").catch(() => {});
  } catch {
    // no navigator at all (a test runner)
  }
}
