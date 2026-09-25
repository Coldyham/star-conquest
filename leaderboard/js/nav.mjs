// The header's collapsible menu (`<details class="menu">` in every page's own
// static header). A native <details> opens and closes on its own summary with
// no JS at all -- the one thing it won't do is close itself when a click lands
// somewhere else on the page, which reads as broken rather than dismissive
// once the menu is the only way to reach "Play"/"All maps"/etc. This is the
// one behaviour every page needs, so it is the one thing here.
export function mountNav() {
  const menu = document.querySelector(".bar .menu");
  if (!menu) return;
  document.addEventListener("click", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
}
