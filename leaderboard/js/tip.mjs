// Tap-to-read for the board's `title` text. A badge like "Bot leads" or
// "Hard" explains itself in its title, which a mouse shows on hover and a
// touch screen never shows at all. So a tap (or pen press) on a titled element
// that isn't itself a control opens the same text in a small bubble under it;
// the next tap anywhere, a scroll or Escape closes it. A link or button keeps
// its tap for what it does (the bot chips filter, the config badge groups),
// and a mouse keeps the browser's own tooltip, so nothing here doubles it.
// Every page gets this through nav.mjs's mountNav.

// A tap on, or inside, one of these does what the control does instead.
const CONTROLS = "a, button, summary, input, select, textarea, label";

/** The element whose title a tap on `node` should show, or null. Takes only
 * `closest`/`getAttribute`, so tests/tip.test.mjs can check it without a DOM. */
export function tipTarget(node) {
  const host = node?.closest?.("[title]");
  if (!host || host.closest(CONTROLS)) return null;
  return (host.getAttribute("title") || "").trim() ? host : null;
}

/** Where the bubble goes: under the element, or above it when the viewport has
 * no room below, and slid sideways to stay `margin` px inside the viewport.
 * Rects are viewport coordinates; the result is too. */
export function tipPlace(anchor, bubble, viewport, gap = 6, margin = 8) {
  const below = anchor.bottom + gap;
  const top = below + bubble.height <= viewport.height - margin || anchor.top - gap - bubble.height < margin
    ? below
    : anchor.top - gap - bubble.height;
  const widest = Math.max(margin, viewport.width - margin - bubble.width);
  const left = Math.min(Math.max(anchor.left, margin), widest);
  return { top, left };
}

export function mountTips() {
  let pointer = "";
  let bubble = null;
  let shownFor = null;

  const hide = () => {
    bubble?.remove();
    bubble = null;
    shownFor = null;
  };

  const show = (host) => {
    bubble = document.createElement("div");
    bubble.className = "tip";
    bubble.setAttribute("role", "tooltip");
    bubble.textContent = host.getAttribute("title");
    document.body.append(bubble);
    const { top, left } = tipPlace(host.getBoundingClientRect(), bubble.getBoundingClientRect(),
      { width: document.documentElement.clientWidth, height: window.innerHeight });
    bubble.style.top = `${top + window.scrollY}px`;
    bubble.style.left = `${left + window.scrollX}px`;
    shownFor = host;
  };

  // pointerdown says what started the click that follows it; a click with no
  // pointer behind it (the keyboard) reads as "" and is left alone too.
  document.addEventListener("pointerdown", (event) => { pointer = event.pointerType; }, true);
  document.addEventListener("click", (event) => {
    const host = pointer === "touch" || pointer === "pen" ? tipTarget(event.target) : null;
    const again = host && host === shownFor;
    hide();
    if (host && !again) show(host);
  });
  window.addEventListener("scroll", hide, { passive: true });
  window.addEventListener("resize", hide);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });
}
