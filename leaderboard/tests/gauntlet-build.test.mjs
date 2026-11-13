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
  GAUNTLET_ID, LIMITS, addStage, botWins, buildBody, canonicalJson, checkBody, checkByName, checkTarget,
  draftCells, draftFromBody, draftNames, draftProblem, dropLane, emptyDraft, emptyLane, emptyStage, fileName, gameLink,
  joinLane, laneBack, laneFrom, linkTarget, listingOf, meetsTarget, planAdd, planMove, postedBest, parentsOf, readDraft,
  removeStage, setStart, mintId, setupProblem, slugOf, stageName, stageSetup, targetText, textLength, tidy,
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
    name: "", by_name: "", wants_listing: false, lives: 1,
    stages: [
      { key: 0, link: "", settings: null, title: "", briefing: [""], hint: "", target: null, bonus_life: null },
      { key: 1, link: "", settings: null, title: "t", briefing: ["a", ""], hint: "", target: null, bonus_life: null },
    ],
    lanes: [emptyLane(0, 1)],
    start: [0],
    next_key: 2,
  });
  assert.deepEqual(readDraft('{"stages": []}').stages, [emptyStage()]);
  // A kept target comes back as it was, a bad one as none; an older draft's aim is ignored.
  const kept = readDraft(JSON.stringify({ stages: [
    { aim: "bot", target: { turns: 9, lost: 1, bot: "marshal", beat: true } },
    { aim: "link", target: { turns: 0 } },
    { aim: "sideways", target: { turns: 9 } },
    { target: { turns: 12, lost: 3 } },
  ] })).stages;
  assert.deepEqual(kept.map((s) => s.target), [
    { turns: 9, lost: 1, bot: "marshal", beat: true }, null, { turns: 9 }, { turns: 12, lost: 3 }]);
  assert.equal(kept.some((s) => "aim" in s), false);
});

test("a draft names its first problem, link problems first", () => {
  const draft = emptyDraft();
  assert.match(draftProblem(draft), /Stage 1 needs a game link/);
  draft.stages[0].link = "garbage";
  assert.match(draftProblem(draft), /Stage 1's link can't be used/);
  draft.stages[0].title = "Opening";
  assert.match(draftProblem(draft), /Opening's link can't be used/, "a titled stage goes by its title");
  draft.stages[0].settings = { ...SETUP };
  assert.match(draftProblem(draft), /needs a name/);
  draft.name = "Fine";
  assert.equal(draftProblem(draft), "");
  draft.by_name = "x".repeat(33);
  assert.match(draftProblem(draft), /your name is over 32/);
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

test("the bots' wins fill a target: the fastest, and the slowest once two bots won", () => {
  const rows = [
    { bot: "heuristic", won: true, turns: 40, lost: 9 },
    { bot: "heuristic", won: true, turns: 35, lost: 9 },   // its own best stands for it
    { bot: "thinker", won: true, turns: 31, lost: 12 },
    { bot: "marshal", won: true, turns: 31, lost: 14 },
    { bot: "knower", won: false, turns: 80, lost: 0 },
  ];
  assert.deepEqual(botWins(rows), {
    fastest: { turns: 31, lost: 12, bot: "thinker" },
    slowest: { turns: 35, lost: 9, bot: "heuristic" },
    wins: 3,
  });
  assert.equal(targetText({ turns: 31, lost: 12, bot: "thinker", beat: true }), "Beat thinker: 31 turns / 12 lost");
  assert.deepEqual(botWins([{ bot: "marshal", won: true, turns: 20, lost: 1 }, { bot: "knower", won: false, turns: 80 }]),
                   { fastest: { turns: 20, lost: 1, bot: "marshal" }, slowest: null, wins: 1 });
  assert.deepEqual(botWins([]), { fastest: null, slowest: null, wins: 0 });
  assert.deepEqual(botWins(null), { fastest: null, slowest: null, wins: 0 });
});

test("the best posted score fills a target, the best of every key the setup is filed under", () => {
  assert.deepEqual(postedBest([
    { best_turns: 18, best_lost: 4, best_by_name: "Old", best_user_name: "Ada" },
    { best_turns: null, best_lost: null, best_by_name: null, best_user_name: null },
    { best_turns: 18, best_lost: 2, best_by_name: " Bo ", best_user_name: null },
  ]), { turns: 18, lost: 2, by: "Bo" });
  assert.equal(postedBest([{ best_turns: null }]), null);
  assert.equal(postedBest([]), null);
  assert.equal(postedBest(undefined), null);
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

test("a draft's target builds into the body and opens again as it was", async () => {
  const draft = emptyDraft();
  draft.name = "Aimed";
  draft.stages = [{ ...emptyStage(), settings: { ...SETUP }, target: { turns: 31, lost: 12, bot: "thinker", beat: true } },
                  { ...emptyStage(), settings: { ...SETUP, seed: 7 }, target: { turns: 9, lost: 0 } },
                  { ...emptyStage(), settings: { ...SETUP, seed: 8 } }];
  const built = checkBody(buildBody(draft));
  assert.deepEqual(built.stages.map((s) => s.target), [
    { turns: 31, lost: 12, bot: "thinker", beat: true }, { turns: 9, lost: 0 }, undefined]);
  const back = await draftFromBody(built, async () => "link");
  assert.deepEqual(back.stages.map((s) => s.target), [
    { turns: 31, lost: 12, bot: "thinker", beat: true }, { turns: 9, lost: 0 }, null]);
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
  draft.stages = [draft.stages[0], draft.stages[2], draft.stages[1]];   // A, C, B: the lanes still run A→B→C
  assert.deepEqual(checkBody(buildBody(draft)).lanes, [[0, 2], [2, 1]]);
  removeStage(draft, b);
  assert.deepEqual(draft.lanes, [], "B's lanes go with it");
  assert.match(draftProblem(draft), /Stage 2 can't be reached/);
  joinLane(draft, a, c);
  assert.equal(draftProblem(draft), "");
  removeStage(draft, a);
  assert.deepEqual(draft.start, []);
  assert.match(draftProblem(draft), /tick at least one stage/);
  setStart(draft, c, true);
  assert.equal(draftProblem(draft), "");
});

/** A, then B and C forking from it, rejoining at D: "1", "2-1", "2-2", "3". */
function fork() {
  const draft = linear(4);
  const [a, b, c, d] = draft.stages.map((s) => s.key);
  draft.lanes = [emptyLane(a, b), emptyLane(a, c), emptyLane(b, d), emptyLane(c, d)];
  return { draft, a, b, c, d };
}

const cellsOf = (draft) => Object.fromEntries([...draftCells(draft)].map(([k, cell]) => [k, cell]));
const namesOf = (draft) => [...draftNames(draft).values()];

test("a stage is named for its place on the graph until it is titled", () => {
  const { draft, a, b, c, d } = fork();
  assert.deepEqual(cellsOf(draft), { [a]: [0, 0], [b]: [1, 0], [c]: [1, 1], [d]: [2, 0] });
  assert.deepEqual(namesOf(draft), ["1", "2-1", "2-2", "3"]);
  assert.equal(stageName(draft, draft.stages[1]), "Stage 2-1");
  draft.stages[1].title = "  The fork ";
  assert.equal(stageName(draft, draft.stages[1]), "The fork");
  assert.deepEqual([...draftNames(draft, [a, b, d]).values()], ["1", "2", "3"], "counted over the stages shown");
  assert.deepEqual(namesOf(linear(3)), ["1", "2", "3"]);
});

test("a dashed box adds a stage at the foot of its column, joined so it lands there", () => {
  const { draft, a, b, c, d } = fork();
  const start = planAdd(draft, 0, { from: b });
  assert.equal(start.how, "open from the start");
  assert.deepEqual(draftCells(start.draft).get(start.stage.key), [0, 1]);
  assert.deepEqual(start.draft.start, [a, start.stage.key]);
  const after = planAdd(draft, 2, { from: b });
  assert.equal(after.how, "from Stage 2-1");
  assert.deepEqual(parentsOf(after.draft, after.stage.key), [b]);
  const beside = planAdd(draft, 1, { from: c });
  assert.equal(beside.how, "from Stage 1, beside Stage 2-2");
  assert.deepEqual(parentsOf(beside.draft, beside.stage.key), [a]);
  assert.deepEqual(draftCells(beside.draft).get(beside.stage.key), [1, 2]);
  const last = planAdd(draft, 3, { from: a });
  assert.equal(last.how, "from Stage 3", "from the lowest stage in the column before");
  assert.deepEqual(parentsOf(last.draft, last.stage.key), [d]);
  assert.equal(planAdd(draft, 5, { from: a }), null);
  assert.equal(draft.stages.length, 4, "a plan never touches the draft it was made from");
  assert.equal(new Set(after.draft.stages.map((s) => s.key)).size, 5);
});

test("in link mode a dashed box adds a lane from the selected stage, no further than one column on", () => {
  const { draft, a, b } = fork();
  const near = planAdd(draft, 1, { from: a, link: true });
  assert.deepEqual(parentsOf(near.draft, near.stage.key), [a]);
  const back = planAdd(draft, 1, { from: b, link: true });
  assert.equal(back.how, "from Stage 1, beside Stage 2-1, with a lane from Stage 2-1");
  assert.deepEqual(parentsOf(back.draft, back.stage.key).sort(), [a, b].sort());
  assert.equal(planAdd(draft, 3, { from: a, link: true }), null, "a lane from column 1 lands in column 2");
});

test("an arrow moves a stage a row by list order, where its column lets it", () => {
  const { draft, b, c, d } = fork();
  assert.equal(planMove(draft, b, "up"), null);
  const down = planMove(draft, b, "down");
  assert.equal(down.note, "Moved Stage 2-1 down a row, past Stage 2-2.");
  assert.deepEqual(draftCells(down.draft).get(b), [1, 1]);
  assert.equal(planMove(draft, d, "down"), null);
  // Two stages in column 3 from different parents keep their parents' order.
  const split = fork();
  split.draft.lanes = split.draft.lanes.filter((l) => l.from !== split.c);
  const e = addStage(split.draft);
  split.draft.lanes = split.draft.lanes.filter((l) => l.to !== e.key).concat([emptyLane(split.c, e.key)]);
  assert.deepEqual(draftCells(split.draft).get(e.key), [2, 1]);
  assert.equal(planMove(split.draft, e.key, "up"), null, "a row follows where its stage comes from");
  assert.ok(c);
});

test("an arrow moves a stage a column by changing the lanes that hold it there", () => {
  const { draft, a, b, c, d } = fork();
  assert.equal(planMove(draft, a, "left"), null);
  assert.equal(planMove(draft, a, "right"), null, "nothing else in its column to hang it from");
  const right = planMove(draft, c, "right");
  assert.equal(right.note, "Moved Stage 2-2 to column 3: it is reached from Stage 2-1, and its lane from Stage 1 went.");
  assert.deepEqual(draftCells(right.draft).get(c), [2, 0]);
  assert.deepEqual(parentsOf(right.draft, c), [b]);
  assert.deepEqual(draftCells(right.draft).get(d), [2, 1], "D is still reached from B, so it stays");
  const left = planMove(draft, b, "left");
  assert.equal(left.note, "Moved Stage 2-1 to column 1: it is open from the start, and its lane from Stage 1 went.");
  assert.deepEqual(left.draft.start, [a, b]);
  assert.deepEqual(draftCells(left.draft).get(b), [0, 1]);
  const back = planMove(draft, d, "left");
  assert.equal(back.note, "Moved Stage 3 to column 2: it is reached from Stage 1, and its lane from Stage 2-1 and Stage 2-2 went.");
  assert.deepEqual(parentsOf(back.draft, d), [a]);
  // Right out of the first column needs another start there.
  const two = planMove(left.draft, a, "right");
  assert.deepEqual(two.draft.start, [b]);
  assert.match(two.note, /no longer open from the start/);
  assert.equal(draftProblem({ ...two.draft, name: "Moved" }), "");
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
  Object.assign(joinLane(draft, a, c), { target: { turns: 12 }, hidden: true });
  Object.assign(laneFrom(draft, a, b), { target: { turns: 31, lost: 12, bot: "thinker", beat: true } });
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
  assert.equal(gated.target.beat, true);
  assert.equal(draft.lanes.find((l) => l.from === 1 && l.to === 3).target.beat, undefined);
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
    { from: 4, to: 9, two_way: false, target: { turns: 5 }, hidden: true },
    { from: 9, to: 2, two_way: false, target: { turns: 5 }, hidden: true },
    { from: 4, to: 2, two_way: false, target: null, hidden: false },
  ]);
  // Keys that don't hold make the draft linear again.
  const relinked = readDraft(JSON.stringify({ stages: [{ key: 1 }, { key: 1 }], lanes: [], start: [] }));
  assert.deepEqual(relinked.stages.map((s) => s.key), [0, 1]);
  assert.deepEqual(relinked.lanes, [emptyLane(0, 1)]);
  assert.deepEqual(relinked.start, [0]);
});

test("lives are kept from 1 to 9 and written only when not 1", () => {
  assert.equal(checkBody(body({ lives: 3 })).lives, 3);
  assert.equal("lives" in checkBody(body({ lives: 1 })), false);
  assert.equal("lives" in checkBody(body()), false);
  for (const bad of [0, 10, 2.5, "3", true]) assert.match(checkBody(body({ lives: bad })), /lives must be/);
  const draft = { ...emptyDraft(), name: "L", stages: [{ ...emptyStage(0), settings: { ...SETUP } }] };
  assert.equal("lives" in buildBody(draft), false);
  assert.equal(buildBody({ ...draft, lives: 4 }).lives, 4);
  assert.equal(readDraft(JSON.stringify({ ...draft, lives: 40 })).lives, LIMITS.lives);
  assert.equal(readDraft(JSON.stringify({ ...draft, lives: "x" })).lives, 1);
});

test("a stage's bonus life is written only where it differs from the graph's default", async () => {
  const draft = { ...emptyDraft(), name: "B", next_key: 3, start: [0],
    stages: [0, 1, 2].map((k) => ({ ...emptyStage(k), settings: { ...SETUP, seed: k } })),
    lanes: [emptyLane(0, 1), { ...emptyLane(0, 2), target: { turns: 9 } }] };
  // 2 is a dead end behind a gate: on by default, so a tick says nothing.
  assert.equal(buildBody(draft).stages.some((s) => "bonus_life" in s), false);
  draft.stages[2].bonus_life = true;
  assert.equal(buildBody(draft).stages.some((s) => "bonus_life" in s), false);
  draft.stages[2].bonus_life = false;
  draft.stages[1].bonus_life = true;
  const built = buildBody(draft);
  assert.equal(built.stages[2].bonus_life, false);
  assert.equal(built.stages[1].bonus_life, true);
  assert.equal("bonus_life" in built.stages[0], false);
  assert.equal(checkBody(built).stages[2].bonus_life, false);
  assert.match(checkBody(body({ stages: [stage({ bonus_life: "yes" })] })), /bonus_life is not true or false/);
  const back = await draftFromBody(checkBody(built), async () => "link");
  assert.deepEqual(back.stages.map((s) => s.bonus_life), [null, true, false]);
  assert.deepEqual(buildBody({ ...back, name: "B" }).stages.map((s) => s.bonus_life), [undefined, true, false]);
});
