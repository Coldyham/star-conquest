// The weekly campaign's map, as circles on lanes: the board's campaign page
// (`leaderboard/js/campaign-page.mjs`, which imports this as
// /play/js/campaignmap.mjs) and the game's win screen (`campaign.graph`) both
// draw with it, so the two pictures are one. Its look is
// `css/campaignmap.css`, which both pages load. It decides nothing: it is
// handed the nodes at their map positions and the lanes between them.
//
// A view is `{nodes: [{key, x, y, r, hue, kind, text, aria, title?, marks?}],
// lanes: [[key, key]]}`. `hue` is the holder's (`campaign.playerHue`), null for
// nobody; `r` the circle's radius (`campaign.nodeRadius`); `text` what is
// written in it. Marks become classes on the node: `open` (a win there would
// be a move), `selected`, `moved` (a gold ring: the replay's last move, or the
// node the game just took).

const SVG = "http://www.w3.org/2000/svg";
/** Page units around the outermost node. */
export const PAD = 40;

/** A node's fill: its holder's colour, or the unclaimed grey. */
export const fillFor = (hue) => (hue === null || hue === undefined ? "#3a3f55" : `hsl(${hue} 70% 58%)`);

/** A node's classes: `node`, its kind, `neutral` when nobody holds it, and its marks. */
export function nodeClasses(node) {
  return ["node", node.kind, node.hue === null || node.hue === undefined ? "neutral" : "", ...(node.marks || [])]
    .filter(Boolean).join(" ");
}

/** The box round `nodes`, `pad` units out: `[x0, y0, width, height]`. */
export function frame(nodes, pad = PAD) {
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);
  const x0 = Math.min(...xs) - pad;
  const y0 = Math.min(...ys) - pad;
  return [x0, y0, Math.max(...xs) - x0 + pad, Math.max(...ys) - y0 + pad];
}

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  for (const child of children) node.append(child);
  return node;
}

/**
 * Draw `view` into `box`, replacing what was there. A click, Enter or Space on
 * a node calls `onPress(key)`; `label` names the map for a screen reader.
 */
export function drawCampaignMap(box, view, { onPress = null, label = "", pad = PAD } = {}) {
  const nodes = view?.nodes || [];
  const at = new Map(nodes.map((n) => [n.key, n]));
  const lanes = (view?.lanes || []).filter(([a, b]) => at.has(a) && at.has(b)).map(([a, b]) => svg("line", {
    class: "lane", x1: at.get(a).x, y1: at.get(a).y, x2: at.get(b).x, y2: at.get(b).y,
  }));
  const circles = nodes.map((node) => {
    const group = svg("g", { class: nodeClasses(node), tabindex: "0", role: "button", "aria-label": node.aria || "" }, [
      svg("circle", { class: "ring", cx: node.x, cy: node.y, r: node.r + 6 }),
      svg("circle", { class: "body", cx: node.x, cy: node.y, r: node.r, fill: fillFor(node.hue) }),
      svg("text", { x: node.x, y: node.y }, [document.createTextNode(node.text)]),
      ...(node.title ? [svg("title", {}, [document.createTextNode(node.title)])] : []),
    ]);
    if (onPress) {
      group.addEventListener("click", () => onPress(node.key));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onPress(node.key); }
      });
    }
    return group;
  });
  const map = nodes.length ? svg("svg", {
    viewBox: frame(nodes, pad).join(" "), role: "group", ...(label ? { "aria-label": label } : {}),
  }, [...lanes, ...circles]) : null;
  box.replaceChildren(...(map ? [map] : []));
  return map;
}
