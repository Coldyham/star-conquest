// The header's collapsible menu (`<details class="menu">` in every page's own
// static header). A native <details> opens and closes on its own summary with
// no JS at all -- the one thing it won't do is close itself when a click lands
// somewhere else on the page, which reads as broken rather than dismissive
// once the menu is the only way to reach "Play"/"All maps"/etc. Every page
// calls this, so it also does the other thing every page needs: the app wiring
// below.
export function mountNav() {
  mountApp();
  const menu = document.querySelector(".bar .menu");
  if (!menu) return;
  document.addEventListener("click", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
}

// The board is half of the installable app (the manifest and service worker at
// the site root, `tools/pwa/`). Registering the worker here is what lets a
// browser offer the install from a board page, not only from /game/. Inside
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
