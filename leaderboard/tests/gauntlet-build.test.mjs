// The gauntlet builder's pure half: what the page refuses before Save, and what
// /api/gauntlet stores — the same `checkBody`, so one set of tests pins both.
//
// fixtures/gauntlet.json is a gauntlet built here from three real game links,
// which tests/test_gauntlet.py reads with the game's own `Gauntlet.from_dict`,
// so a drift between what this writes and what the game reads fails on one side
// or the other. Regenerate it after a deliberate change to the build:
//   UPDATE_FIXTURES=1 node --test leaderboard/tests/gauntlet-build.test.mjs

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

import {
  GAUNTLET_ID, LIMITS, buildBody, canonicalJson, checkBody, checkByName, draftFromBody, draftProblem,
  emptyDraft, emptyStage, fileName, gameLink, moveStage, readDraft,
  mintId, setupProblem, slugOf, stageSetup, textLength, tidy,
} from "../js/gauntlet-build.mjs";
import { encodeToken } from "../js/token-encode.mjs";

const FIXTURE = fileURLToPath(new URL("./fixtures/gauntlet.json", import.meta.url));
const fixture = JSON.parse(readFileSync(FIXTURE, "utf8"));
const subtle = webcrypto.subtle;
const deflate = (bytes) => new Uint8Array(deflateSync(bytes, { level: 9 }));

const SETUP = { mode: "random", players: 2, nodes: 13, seed: 42 };

function stage(over = {}) {
  return { id: 0, title: "One", briefing: ["Take it all."], hint: "Go.", settings: { ...SETUP }, ...over };
}

function body(over = {}) {
  return { name: "Hard mode", stages: [stage()], ...over };
}

test("the fixture is what the build makes of its draft today", async () => {
  const built = checkBody(buildBody(fixture.draft));
  assert.equal(typeof built, "object", built);
  const id = await mintId(built, subtle);
  const stored = { id, ...built };
  const inline = await encodeToken(stored, deflate);
  if (process.env.UPDATE_FIXTURES) {
    writeFileSync(FIXTURE, JSON.stringify({ ...fixture, id, body: stored, inline }, null, 2) + "\n");
    return;
  }
  assert.equal(id, fixture.id);
  assert.deepEqual(stored, fixture.body);
  assert.match(fixture.id, GAUNTLET_ID);
  // The inline form decodes back to the stored body (encoders may differ in
  // bytes, so compare what it says, not how it is spelled).
  const bytes = Buffer.from(fixture.inline.replace(/-/g, "+").replace(/_/g, "/"), "base64");
  assert.deepEqual(JSON.parse(inflateSync(bytes).toString("utf8")), fixture.body);
  assert.deepEqual(JSON.parse(inflateSync(Buffer.from(
    inline.replace(/-/g, "+").replace(/_/g, "/"), "base64")).toString("utf8")), fixture.body);
});

test("a built body numbers stages in play order, tidies text and drops blank paragraphs", () => {
  const out = buildBody({
    name: "  Two   words ",
    stages: [
      { title: " a  b ", briefing: ["x  y", "   ", ""], hint: " h ", settings: { ...SETUP, challenge: { turns: 9 }, autoplay: true } },
      { title: "", briefing: undefined, hint: undefined, settings: SETUP },
    ],
  });
  assert.deepEqual(out, {
    name: "Two words",
    stages: [
      { id: 0, title: "a b", briefing: ["x y"], hint: "h", settings: SETUP },
      { id: 1, title: "", briefing: [], hint: "", settings: SETUP },
    ],
  });
});

test("a stage never carries a score to beat or autoplay", () => {
  assert.deepEqual(stageSetup({ ...SETUP, challenge: { turns: 3 }, autoplay: true }), SETUP);
  const out = checkBody(body({ stages: [stage({ settings: { ...SETUP, challenge: { turns: 1 } } })] }));
  assert.deepEqual(out.stages[0].settings, SETUP);
});

test("text is measured as the game measures it: collapsed, in code points", () => {
  assert.equal(tidy("  a \n\t b  "), "a b");
  assert.equal(tidy(7), "");
  assert.equal(textLength("café"), 4);
  assert.equal(textLength("🚀🚀"), 2);
  const title = "🚀".repeat(LIMITS.title);
  assert.equal(typeof checkBody(body({ stages: [stage({ title })] })), "object");
  assert.match(checkBody(body({ stages: [stage({ title: title + "x" })] })), /title is over 32/);
});

test("every limit the game clamps is refused here instead", () => {
  const long = (n) => "x".repeat(n);
  const cases = [
    [body({ name: "" }), /needs a name/],
    [body({ name: long(33) }), /name is over 32/],
    [body({ stages: [] }), /no stages/],
    [body({ stages: Array.from({ length: 65 }, (_, i) => stage({ id: i })) }), /at most 64/],
    [body({ stages: [stage({ hint: long(91) })] }), /hint is over 90/],
    [body({ stages: [stage({ briefing: Array(7).fill("p") })] }), /more than 6/],
    [body({ stages: [stage({ briefing: ["ok", long(241)] })] }), /paragraph 2 is over 240/],
    [body({ stages: [stage({ briefing: "one string" })] }), /not a list/],
    [body({ stages: [stage(), stage()] }), /stage 2 has a bad id/],
    [body({ stages: [stage({ id: -1 })] }), /bad id/],
    [body({ stages: ["nope"] }), /not a stage/],
    [null, /not a gauntlet/],
    [[], /not a gauntlet/],
  ];
  for (const [input, want] of cases) assert.match(String(checkBody(input)), want);
  // Exactly at the limits is fine.
  const edge = body({ name: long(32), stages: [stage({ title: long(32), hint: long(90),
    briefing: Array(6).fill(long(240)) })] });
  assert.equal(typeof checkBody(edge), "object");
});

test("a setup must be one fixed map", () => {
  assert.equal(setupProblem(SETUP), "");
  assert.match(setupProblem({ ...SETUP, seed: null }), /rolls a new map/);
  assert.match(setupProblem({ ...SETUP, seed: 1.5 }), /rolls a new map/);
  assert.match(setupProblem({ ...SETUP, players: 0 }), /player count/);
  assert.match(setupProblem({ ...SETUP, nodes: "13" }), /system count/);
  assert.match(setupProblem([]), /no setup/);
  assert.match(checkBody(body({ stages: [stage({ settings: { ...SETUP, seed: null } })] })), /stage 1 rolls/);
});

test("only the keys the game reads are kept, and lanes and start must name real stages", () => {
  const out = checkBody({ ...body({ stages: [stage(), stage({ id: 5 })] }), id: "mine", extra: 1,
                          lanes: [[0, 5]], start: [0] });
  assert.deepEqual(Object.keys(out), ["name", "stages", "lanes", "start"]);
  assert.deepEqual(Object.keys(out.stages[0]), ["id", "title", "briefing", "hint", "settings"]);
  assert.match(checkBody({ ...body(), lanes: [[0, 9]] }), /not there/);
  assert.match(checkBody({ ...body(), lanes: [[0, 0]] }), /not there/);
  assert.match(checkBody({ ...body(), lanes: [[0]] }), /not a pair/);
  assert.match(checkBody({ ...body(), start: [3] }), /start names/);
});

test("a lane runs one way unless it says two_way, and every stage must be reachable", () => {
  const three = { stages: [stage(), stage({ id: 1 }), stage({ id: 2 })] };
  assert.deepEqual(checkBody(body({ ...three, lanes: [[0, 1], [1, 2, { two_way: false }]] })).lanes, [[0, 1], [1, 2]]);
  assert.deepEqual(checkBody(body({ ...three, lanes: [[0, 1], [2, 1, { two_way: true }]] })).lanes,
                   [[0, 1], [2, 1, { two_way: true }]]);
  assert.match(checkBody(body({ ...three, lanes: [[0, 1], [2, 1]] })), /stage 3 can't be reached/);
  assert.match(checkBody(body({ ...three, lanes: [[0, 1], [1, 2]], start: [1] })), /stage 1 can't be reached/);
  assert.match(checkBody(body({ ...three, lanes: [[0, 1]] })), /stage 3 can't be reached/);
  assert.match(checkBody(body({ ...three, lanes: [[0, 1], [1, 2, { turns: 9 }]] })), /only say two_way/);
  assert.match(checkBody(body({ ...three, lanes: [[0, 1], [1, 2, "both"]] })), /only say two_way/);
  assert.equal(typeof checkBody(body(three)), "object");
});

test("a body over 64 KiB is refused", () => {
  const big = { ...SETUP, custom_map: { blob: "x".repeat(LIMITS.bodyBytes) } };
  assert.match(checkBody(body({ stages: [stage({ settings: big })] })), /over 64 KiB/);
});

test("slugs are short readable lowercase words, never empty", () => {
  assert.equal(slugOf("Hard Mode!"), "hard-mode");
  assert.equal(slugOf("Café Über"), "cafe-uber");
  assert.equal(slugOf("The longest possible gauntlet name"), "the-longest");
  assert.equal(slugOf("Supercalifragilisticexpialidocious"), "supercalifragilisti");
  assert.equal(slugOf("!!!"), "gauntlet");
  assert.equal(slugOf(""), "gauntlet");
  for (const name of ["Hard Mode!", "Supercalifragilisticexpialidocious", "a b c d e f g h i j k"]) {
    assert.ok(slugOf(name).length <= LIMITS.slug);
  }
});

test("the id is the slug plus a hash of the content, and fits the game's 32 characters", async () => {
  const a = checkBody(body());
  const id = await mintId(a, subtle);
  assert.match(id, GAUNTLET_ID);
  assert.ok(id.startsWith("hard-mode-"));
  assert.ok(id.length <= 32);
  // Key order and an `id` already on the body change nothing; content does.
  const reordered = JSON.parse(canonicalJson(a));
  assert.equal(await mintId({ id: "whatever", ...reordered }, subtle), id);
  const edited = checkBody(body({ stages: [stage({ hint: "Different." })] }));
  assert.notEqual(await mintId(edited, subtle), id);
  const longest = checkBody(body({ name: "Supercalifragilisticexpialidocio" }));
  assert.equal((await mintId(longest, subtle)).length, 32);
});

test("canonical JSON sorts object keys at every depth and keeps array order", () => {
  assert.equal(canonicalJson({ b: 1, a: [{ d: 1, c: 2 }, 3] }), '{"a":[{"c":2,"d":1},3],"b":1}');
});

test("an author's name is optional, tidied, and at most 32 characters", () => {
  assert.equal(checkByName(undefined), "");
  assert.equal(checkByName("  Ada  L "), "Ada L");
  assert.equal(checkByName("x".repeat(33)), null);
});

test("links and file names", () => {
  assert.equal(gameLink("https://example.app/game/", "hard-mode-0123456789ab"),
               "https://example.app/game/#g=hard-mode-0123456789ab");
  assert.equal(fileName({ name: "Hard Mode" }, "hard-mode-0123456789ab"), "hard-mode-0123456789ab.json");
  assert.equal(fileName({ name: "Hard Mode" }, ""), "hard-mode.json");
});

// -- the page's draft ---------------------------------------------------------

test("a stored gauntlet opens as a draft that builds back to the same body", async () => {
  const draft = await draftFromBody(fixture.body, async (s) => `link:${s.seed}`);
  assert.equal(draft.name, fixture.body.name);
  assert.deepEqual(draft.stages.map((s) => s.link), ["link:42", "link:99991", "link:2024"]);
  assert.deepEqual(draft.stages[1].briefing, [""]);   // an empty briefing still offers a box
  assert.deepEqual(checkBody(buildBody(draft)), checkBody(fixture.body));
  assert.equal(draftProblem(draft), "");
});

test("a draft read back from storage is tolerant of anything", () => {
  for (const junk of ["", "nope", "[]", "{}", '{"stages": 3}']) assert.equal(readDraft(junk), null);
  const out = readDraft(JSON.stringify({ name: 5, wants_listing: "yes",
    stages: [null, { link: 3, briefing: [], settings: [] }, { title: "t", briefing: ["a", 2] }] }));
  assert.deepEqual(out, {
    name: "", by_name: "", wants_listing: false,
    stages: [
      { link: "", settings: null, title: "", briefing: [""], hint: "" },
      { link: "", settings: null, title: "t", briefing: ["a", ""], hint: "" },
    ],
  });
  assert.deepEqual(readDraft('{"stages": []}').stages, [emptyStage()]);
});

test("a draft names its first problem, link problems first", () => {
  const draft = emptyDraft();
  assert.match(draftProblem(draft), /stage 1 needs a game link/);
  draft.stages[0].link = "garbage";
  assert.match(draftProblem(draft), /stage 1's link can't be used/);
  draft.stages[0].settings = { ...SETUP };
  assert.match(draftProblem(draft), /needs a name/);
  draft.name = "Fine";
  assert.equal(draftProblem(draft), "");
  draft.by_name = "x".repeat(33);
  assert.match(draftProblem(draft), /your name is over 32/);
});

test("moving a stage swaps it with its neighbour, and not past either end", () => {
  assert.deepEqual(moveStage(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveStage(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveStage(["a", "b", "c"], 0, -1), ["a", "b", "c"]);
  assert.deepEqual(moveStage(["a", "b", "c"], 2, 1), ["a", "b", "c"]);
});
