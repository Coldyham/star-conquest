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
  GAUNTLET_ID, LIMITS, addStage, botTarget, buildBody, canonicalJson, checkBody, checkByName, checkTarget,
  draftFromBody, draftProblem, dropLane, emptyDraft, emptyLane, emptyStage, fileName, gameLink, joinLane, laneBack,
  laneFrom, linkTarget, listingOf, meetsTarget, moveStage, parentsOf, readDraft, removeStage, setStart, mintId, setupProblem,
  slugOf, stageSetup, targetText, textLength, tidy,
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
  assert.match(checkBody(body({ ...three, lanes: [[0, 1], [1, 2, { turns: 9, x: 1 }]] })), /can only say/);
  assert.match(checkBody(body({ ...three, lanes: [[0, 1], [1, 2, "both"]] })), /can only say/);
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
      { key: 0, link: "", settings: null, title: "", briefing: [""], hint: "", aim: "none", target: null },
      { key: 1, link: "", settings: null, title: "t", briefing: ["a", ""], hint: "", aim: "none", target: null },
    ],
    lanes: [emptyLane(0, 1)],
    start: [0],
    next_key: 2,
  });
  assert.deepEqual(readDraft('{"stages": []}').stages, [emptyStage()]);
  // A kept target comes back with how it was chosen; a bad one, or a bad aim, as none.
  const kept = readDraft(JSON.stringify({ stages: [
    { aim: "bot", target: { turns: 9, lost: 1, bot: "marshal", beat: true } },
    { aim: "link", target: { turns: 0 } },
    { aim: "sideways", target: { turns: 9 } },
    { aim: "turns", target: { turns: 12, lost: 3 } },
  ] })).stages;
  assert.deepEqual(kept.map((s) => [s.aim, s.target]), [
    ["bot", { turns: 9, lost: 1, bot: "marshal", beat: true }], ["none", null], ["none", { turns: 9 }],
    ["turns", { turns: 12, lost: 3 }]]);
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

// -- stage targets ------------------------------------------------------------

test("a target is kept as the game reads it, and a malformed one refused", () => {
  const out = checkBody(body({ stages: [stage({ target: { turns: 14, lost: 3, bot: "  thinker ", beat: true } })] }));
  assert.deepEqual(out.stages[0].target, { turns: 14, lost: 3, bot: "thinker", beat: true });
  assert.deepEqual(checkBody(body({ stages: [stage({ target: { turns: 9, beat: false } })] })).stages[0].target,
                   { turns: 9 });
  assert.equal("target" in checkBody(body({ stages: [stage({ target: null })] })).stages[0], false);
  for (const bad of [5, { turns: 0 }, { turns: 1.5 }, { turns: 9, lost: -1 }, { turns: 9, extra: 1 },
                     { turns: 9, beat: "yes" }, { turns: 9, bot: 3 }, { turns: 9, bot: "x".repeat(25) }]) {
    assert.match(checkBody(body({ stages: [stage({ target: bad })] })), /^stage 1 has a target/, JSON.stringify(bad));
  }
  assert.equal(typeof checkTarget({ turns: 9, bot: "x".repeat(24) }), "object");
});

test("a pasted challenge's score is a target to meet", () => {
  assert.deepEqual(linkTarget({ turns: 14, lost: 3, hand: 14, by: "Ada" }), { turns: 14, lost: 3 });
  assert.equal(linkTarget(null), null);
  assert.equal(targetText({ turns: 14, lost: 3 }), "Win within 14 turns / 3 lost");
  assert.equal(targetText({ turns: 14 }), "Win in 14 turns or fewer");
});

test("the best bot's win is a target to beat; no win or no rows is none", () => {
  const rows = [
    { bot: "heuristic", won: true, turns: 40, lost: 9 },
    { bot: "thinker", won: true, turns: 31, lost: 12 },
    { bot: "marshal", won: true, turns: 31, lost: 14 },
    { bot: "knower", won: false, turns: 80, lost: 0 },
  ];
  const { target } = botTarget(rows);
  assert.deepEqual(target, { turns: 31, lost: 12, bot: "thinker", beat: true });
  assert.equal(targetText(target), "Beat thinker: 31 turns / 12 lost");
  assert.deepEqual(botTarget([{ bot: "knower", won: false, turns: 80, lost: 0 }]), { none: "no-win" });
  assert.deepEqual(botTarget([]), { none: "no-rows" });
});

test("a score meets a target as the game's Target.met decides", () => {
  assert.ok(meetsTarget({ turns: 14, lost: 30 }, { turns: 14 }));
  assert.ok(meetsTarget({ turns: 14, lost: 99 }, { turns: 14 }));
  assert.ok(!meetsTarget({ turns: 15, lost: 0 }, { turns: 14 }));
  assert.ok(meetsTarget({ turns: 14, lost: 30 }, { turns: 14, lost: 30 }));
  assert.ok(meetsTarget({ turns: 13, lost: 99 }, { turns: 14, lost: 30 }));
  assert.ok(!meetsTarget({ turns: 14, lost: 31 }, { turns: 14, lost: 30 }));
  assert.ok(!meetsTarget({ turns: 14 }, { turns: 14, lost: 30 }));
  assert.ok(!meetsTarget({ turns: 31, lost: 12 }, { turns: 31, lost: 12, beat: true }));
  assert.ok(meetsTarget({ turns: 31, lost: 11 }, { turns: 31, lost: 12, beat: true }));
  assert.ok(!meetsTarget(null, { turns: 14 }));
});

test("a draft's target builds into the body and opens again with how it was chosen", async () => {
  const draft = emptyDraft();
  draft.name = "Aimed";
  draft.stages = [{ ...emptyStage(), settings: { ...SETUP }, aim: "bot",
                    target: { turns: 31, lost: 12, bot: "thinker", beat: true } },
                  { ...emptyStage(), settings: { ...SETUP, seed: 7 }, aim: "link", target: { turns: 9, lost: 0 } },
                  { ...emptyStage(), settings: { ...SETUP, seed: 8 } }];
  const built = checkBody(buildBody(draft));
  assert.deepEqual(built.stages.map((s) => s.target), [
    { turns: 31, lost: 12, bot: "thinker", beat: true }, { turns: 9, lost: 0 }, undefined]);
  const back = await draftFromBody(built, async () => "link");
  // The body keeps the link's score, never the challenge link: it opens as a custom target holding it.
  assert.deepEqual(back.stages.map((s) => s.aim), ["bot", "turns", "none"]);
  assert.deepEqual(back.stages[1].target, { turns: 9, lost: 0 });
  assert.deepEqual(checkBody(buildBody(back)), built);
});

test("a listed row reads tolerantly for the public page", () => {
  const row = {
    gauntlet_id: "hard-mode-0123456789ab", by_name: "  Ada  ", created_at: "2026-10-22T10:00:00Z",
    name: "Hard  mode", stages: [{ title: "Opening" }, { title: "" }, null],
  };
  assert.deepEqual(listingOf(row), {
    id: "hard-mode-0123456789ab", name: "Hard mode", by: "Ada",
    titles: ["Opening", "Stage 2", "Stage 3"], created: "2026-10-22T10:00:00Z",
  });
  const bare = listingOf({ gauntlet_id: "x-0123456789ab", stages: "nope" });
  assert.equal(bare.name, "x-0123456789ab");
  assert.deepEqual(bare.titles, []);
  assert.equal(bare.by, "");
});

// fixtures/gauntlet-lanes.json is read by tests/test_gauntlet.py too, so the
// lanes stored here are the lanes the game reads.
const LANES = JSON.parse(readFileSync(fileURLToPath(new URL("./fixtures/gauntlet-lanes.json", import.meta.url)), "utf8"));

test("a lane may carry a target and be hidden, stored as the game reads it", () => {
  const out = checkBody(LANES.body);
  assert.equal(typeof out, "object");
  assert.deepEqual(out.lanes, LANES.stored_lanes);
  assert.deepEqual(out.start, [0]);
});

test("a lane's options are refused when the game could not read them", () => {
  const three = { stages: [stage(), stage({ id: 1 }), stage({ id: 2 })] };
  const lanes = (how) => body({ ...three, lanes: [[0, 1], [1, 2, how]] });
  assert.match(checkBody(lanes({ hidden: true })), /hidden lane needs a target/);
  assert.match(checkBody(lanes({ turns: 0 })), /a lane has a target with no turns/);
  assert.match(checkBody(lanes({ turns: 5, lost: -1 })), /bad ships-lost/);
  assert.match(checkBody(lanes({ turns: 5, hidden: "yes" })), /hidden is not true or false/);
  assert.match(checkBody(lanes({ two_way: 1 })), /two_way is not true or false/);
  assert.match(checkBody(lanes({ turns: 5, colour: "red" })), /can only say/);
  assert.deepEqual(checkBody(lanes({ hidden: false, two_way: false })).lanes, [[0, 1], [1, 2]]);
});

test("a gated lane still counts for reaching a stage", () => {
  const three = { stages: [stage(), stage({ id: 1 }), stage({ id: 2 })] };
  assert.deepEqual(checkBody(body({ ...three, lanes: [[0, 1], [1, 2, { turns: 9, hidden: true }]] })).lanes,
                   [[0, 1], [1, 2, { turns: 9, hidden: true }]]);
});

// -- lanes on the page ---------------------------------------------------------

/** A draft of `n` stages with links, joined in list order, as + Stage makes it. */
function linear(n) {
  const draft = emptyDraft();
  draft.name = "Graph";
  draft.stages[0].settings = { ...SETUP, seed: 100 };
  for (let i = 1; i < n; i++) addStage(draft).settings = { ...SETUP, seed: 100 + i };
  return draft;
}

test("a draft whose lanes were never touched builds today's linear body, so its id holds", async () => {
  const draft = linear(3);
  const built = buildBody(draft);
  assert.equal("lanes" in built, false);
  assert.equal("start" in built, false);
  const before = buildBody({ ...draft, lanes: undefined, start: undefined });
  assert.equal(canonicalJson(built), canonicalJson(before));
  assert.equal(await mintId(checkBody(built), subtle), await mintId(checkBody(before), subtle));
  // The fixture's draft, kept before lanes, still builds what it did.
  assert.deepEqual(checkBody(buildBody(readDraft(JSON.stringify(fixture.draft)))), checkBody(buildBody(fixture.draft)));
});

test("lanes join stages by key, so reordering and removing never rewire one", () => {
  const draft = linear(3);
  const [a, b, c] = draft.stages.map((s) => s.key);
  draft.stages = moveStage(draft.stages, 2, -1);   // A, C, B: the lanes still run A→B→C
  assert.deepEqual(checkBody(buildBody(draft)).lanes, [[0, 2], [2, 1]]);
  removeStage(draft, b);
  assert.deepEqual(draft.lanes, [], "B's lanes go with it");
  assert.match(draftProblem(draft), /stage 2 can't be reached/);
  joinLane(draft, a, c);
  assert.equal(draftProblem(draft), "");
  removeStage(draft, a);
  assert.deepEqual(draft.start, []);
  assert.match(draftProblem(draft), /tick at least one stage/);
  setStart(draft, c, true);
  assert.equal(draftProblem(draft), "");
});

test("+ After and + Branch place a stage and join it", () => {
  const draft = linear(2);
  const [a, b] = draft.stages.map((s) => s.key);
  const fork = addStage(draft, { beside: b });
  assert.deepEqual(draft.stages.map((s) => s.key), [a, b, fork.key]);
  assert.deepEqual(parentsOf(draft, fork.key), [a]);
  const next = addStage(draft, { after: b });
  assert.deepEqual(draft.stages.map((s) => s.key), [a, b, next.key, fork.key]);
  assert.deepEqual(parentsOf(draft, next.key), [b]);
  const twin = addStage(draft, { beside: a });
  assert.deepEqual(parentsOf(draft, twin.key), []);
  assert.deepEqual(draft.start, [a, twin.key], "a branch beside a start is a start");
  assert.equal(new Set(draft.stages.map((s) => s.key)).size, 5, "keys are never reused");
});

test("the reverse pair makes a lane two-way and never adds a second", () => {
  const draft = linear(2);
  const [a, b] = draft.stages.map((s) => s.key);
  assert.equal(laneFrom(draft, b, a), null);
  assert.ok(laneBack(draft, b, a));
  const lane = joinLane(draft, b, a);
  assert.equal(draft.lanes.length, 1);
  assert.equal(lane.two_way, true);
  assert.equal(laneFrom(draft, b, a), lane);
  assert.deepEqual(checkBody(buildBody(draft)).lanes, [[0, 1, { two_way: true }]]);
  dropLane(draft, lane);
  assert.deepEqual(draft.lanes, []);
});

test("a lane's gate, hidden and two-way build as the game reads them", () => {
  const draft = linear(3);
  const [a, b, c] = draft.stages.map((s) => s.key);
  Object.assign(joinLane(draft, a, c), { aim: "turns", target: { turns: 12 }, hidden: true });
  Object.assign(laneFrom(draft, a, b), { aim: "bot", target: { turns: 31, lost: 12, bot: "thinker", beat: true } });
  // Hidden with no target is dropped, as the game would drop it.
  Object.assign(laneFrom(draft, b, c), { hidden: true });
  const built = checkBody(buildBody(draft));
  assert.deepEqual(built.lanes, [[0, 1, { turns: 31, lost: 12, bot: "thinker", beat: true }],
                                 [0, 2, { turns: 12, hidden: true }], [1, 2]]);
  assert.equal("start" in built, false);
});

test("a branching body opens as a draft and builds back to the same body", async () => {
  const stored = checkBody(LANES.body);
  const draft = await draftFromBody(stored, async () => "link");
  assert.equal(draft.lanes.length, LANES.stored_lanes.length);
  assert.deepEqual(draft.start, [0]);
  const gated = draft.lanes.find((l) => l.from === 0 && l.to === 5);
  assert.equal(gated.aim, "bot");
  assert.equal(draft.lanes.find((l) => l.from === 1 && l.to === 3).aim, "turns");
  assert.equal(draft.lanes.find((l) => l.from === 2 && l.to === 4).hidden, true);
  const rebuilt = checkBody(buildBody(draft));
  assert.deepEqual(rebuilt.lanes, [...stored.lanes].sort((x, y) => x[0] - y[0] || x[1] - y[1]));
  // ... and it survives being kept and read back.
  assert.deepEqual(checkBody(buildBody(readDraft(JSON.stringify(draft)))), rebuilt);
  assert.equal(await mintId(rebuilt, subtle), await mintId(checkBody(buildBody(draft)), subtle));
});

test("a kept draft's lanes are read tolerantly", () => {
  const stages = [{ key: 4 }, { key: 9 }, { key: 2 }];
  const out = readDraft(JSON.stringify({
    stages, next_key: 3, start: [9, 77, 9],
    lanes: [{ from: 4, to: 9, aim: "turns", target: { turns: 5 }, hidden: true },
            { from: 9, to: 4 }, { from: 9, to: 2, aim: "sideways", target: { turns: 5 }, hidden: true },
            { from: 2, to: 2 }, { from: 2, to: 77 }, "lane", { from: 4, to: 2, aim: "link", target: { turns: 0 } }],
  }));
  assert.deepEqual(out.stages.map((s) => s.key), [4, 9, 2]);
  assert.equal(out.next_key, 10);
  assert.deepEqual(out.start, [9]);
  assert.deepEqual(out.lanes, [
    { from: 4, to: 9, two_way: false, aim: "turns", target: { turns: 5 }, hidden: true },
    { from: 9, to: 2, two_way: false, aim: "none", target: null, hidden: false },
    { from: 4, to: 2, two_way: false, aim: "link", target: null, hidden: false },
  ]);
  // Keys that don't hold make the draft linear again.
  const relinked = readDraft(JSON.stringify({ stages: [{ key: 1 }, { key: 1 }], lanes: [], start: [] }));
  assert.deepEqual(relinked.stages.map((s) => s.key), [0, 1]);
  assert.deepEqual(relinked.lanes, [emptyLane(0, 1)]);
  assert.deepEqual(relinked.start, [0]);
});
