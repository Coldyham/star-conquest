// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { tipPlace, tipTarget } from "../js/tip.mjs";

/** A stand-in element: its own tag and attributes, and a parent chain that
 * `closest` walks, matching only the selectors tip.mjs asks for. */
function node(tag, attrs = {}, parent = null) {
  const self = {
    tag, parent,
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    closest(selector) {
      const wanted = selector.split(",").map((s) => s.trim());
      for (let at = self; at; at = at.parent) {
        if (wanted.some((s) => (s === "[title]" ? "title" in at.attrs : s === at.tag))) return at;
      }
      return null;
    },
    attrs,
  };
  return self;
}

test("a tap on a titled badge, or on text inside it, shows its title", () => {
  const card = node("div");
  const badge = node("span", { class: "badge bot-lead", title: "knower — 40 turns" }, card);
  assert.equal(tipTarget(badge), badge);
  assert.equal(tipTarget(node("b", {}, badge)), badge);
});

test("a link keeps its tap, even with a title", () => {
  const fog = node("a", { title: "Fog of war" });
  assert.equal(tipTarget(fog), null);
  assert.equal(tipTarget(node("span", { title: "inside a link" }, node("a"))), null);
  assert.equal(tipTarget(node("button", { title: "Random setup" })), null);
});

test("nothing to show without a title", () => {
  assert.equal(tipTarget(node("span", { class: "chip" })), null);
  assert.equal(tipTarget(node("span", { title: "  " })), null);
  assert.equal(tipTarget(null), null);
});

const viewport = { width: 400, height: 800 };

test("the bubble sits under its badge, kept inside the viewport", () => {
  const badge = { top: 100, bottom: 130, left: 50 };
  assert.deepEqual(tipPlace(badge, { width: 200, height: 40 }, viewport), { top: 136, left: 50 });
  // Near the right edge it slides left rather than running off the screen.
  assert.deepEqual(tipPlace({ ...badge, left: 300 }, { width: 200, height: 40 }, viewport), { top: 136, left: 192 });
});

test("with no room below, it goes above", () => {
  const badge = { top: 740, bottom: 770, left: 20 };
  assert.deepEqual(tipPlace(badge, { width: 200, height: 60 }, viewport), { top: 674, left: 20 });
});
