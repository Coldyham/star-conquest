// The lobby page's pure helpers: grouping, the roster, the seat link's shape,
// the local "yours" store, and the wording. The DOM half is exercised by hand
// on a deploy preview.

import assert from "node:assert/strict";
import test from "node:test";

import { phrase } from "../js/matchnames.mjs";
import {
  SEATS_KEY, deadlineLabel, deadlineNote, groupMatches, matchLabel, mineIds, openLink,
  parseSeats, resultNote, roster, seatLink, seatWho, setupLine, waitingNote, withSeat,
} from "../js/pbp.mjs";

const ID = "00112233445566ff";
const base = {
  match_id: ID, seats: [1, 2], turn: 3, finished: false, winner: null,
  waiting: [2], lapsed: {}, deadline_hours: 24, unclaimed: [], names: {}, title: "",
  seed: 42, turn_opened_at: "2026-09-24T00:00:00Z",
  settings_json: { mode: "random", players: 3, nodes: 18, seed: 42, ai_strategy: ["heuristic", "heuristic", "knower"] },
};

test("a match is called by its title, else its pass-phrase", () => {
  assert.equal(matchLabel(base), phrase(ID));
  assert.equal(matchLabel({ ...base, title: "  Friday night " }), "Friday night");
});

test("the setup line reads like a map's own, and says when the map is drawn", () => {
  assert.equal(setupLine(base), "Random · 3 players · 18 systems · seed 42");
  const drawn = { ...base, settings_json: { ...base.settings_json, custom_map: { n: [] } } };
  assert.ok(setupLine(drawn).endsWith("· custom map"));
});

test("the roster names people, open seats and each bot seat's strategy", () => {
  const rows = roster({ ...base, seats: [1, 2], unclaimed: [2], names: { 1: "Alice" } });
  assert.deepEqual(rows.map((r) => [r.seat, r.kind, seatWho(r)]), [
    [1, "person", "Alice"], [2, "open", "open seat"], [3, "bot", "knower (bot)"],
  ]);
  const unnamed = roster({ ...base, settings_json: { players: 2 } });
  assert.equal(seatWho(unnamed[1]), "player");
  const random = roster({ ...base, seats: [1], settings_json: { players: 2, ai_strategy: ["", "random"] } });
  assert.equal(seatWho(random[1]), "random bot");
});

test("each seat's state: to move, in, lapsed, or the winner", () => {
  const live = roster({ ...base, waiting: [2], lapsed: { 2: "bot" } });
  assert.deepEqual(live.map((r) => r.state), ["in", "bot", ""]);
  const over = roster({ ...base, finished: true, winner: 3 });
  assert.deepEqual(over.map((r) => r.state), ["", "", "won"]);
});

test("a result names the winner by name and colour, or says draw", () => {
  const over = { ...base, finished: true, turn: 34, names: { 2: "Bob" } };
  assert.equal(resultNote({ ...over, winner: 2 }), "Won by Bob (Crimson) in 34 turns");
  assert.equal(resultNote({ ...over, winner: 1 }), "Won by Azure in 34 turns");
  assert.equal(resultNote({ ...over, winner: 3 }), "Won by knower (Verdant) in 34 turns");
  assert.equal(resultNote({ ...over, winner: 0 }), "Draw after 34 turns");
  assert.equal(resultNote({ ...over, winner: null }), "Finished after 34 turns");
});

test("the lobby shows open invitations, your live matches and finished ones — nothing else", () => {
  const matches = [
    { ...base, match_id: "a".repeat(16), status: "open" },
    { ...base, match_id: "b".repeat(16), status: "in_progress" },
    { ...base, match_id: "c".repeat(16), status: "lapsed" },
    { ...base, match_id: "d".repeat(16), status: "finished", finished: true },
    { ...base, match_id: "e".repeat(16), status: "open" },
    { ...base, match_id: "b".repeat(16), status: "in_progress" },
  ];
  const groups = groupMatches(matches, { ["b".repeat(16)]: {}, ["e".repeat(16)]: {} });
  assert.deepEqual(groups.open.map((m) => m.match_id[0]), ["a"]);
  assert.deepEqual(groups.mine.map((m) => m.match_id[0]), ["b", "e"]);
  assert.deepEqual(groups.finished.map((m) => m.match_id[0]), ["d"]);
});

const TOKEN = "ab".repeat(16);

test("yours is read straight from the game's seat store", () => {
  assert.equal(SEATS_KEY, "sc_pbp_seats");
  const store = JSON.stringify({ [ID]: { seat: 2, token: TOKEN } });
  assert.deepEqual(parseSeats(store), { [ID]: { seat: 2, token: TOKEN } });
});

test("a malformed seat store reads as empty, and junk entries are skipped", () => {
  assert.deepEqual(parseSeats("not json"), {});
  assert.deepEqual(parseSeats("[1]"), {});
  assert.deepEqual(parseSeats(null), {});
  const junk = {
    [ID]: { seat: 2, token: TOKEN },
    nothex: { seat: 1, token: TOKEN },
    ["f".repeat(16)]: { seat: "2", token: TOKEN },
    ["e".repeat(16)]: { seat: 0, token: TOKEN },
    ["d".repeat(16)]: { seat: 1, token: "short" },
    ["c".repeat(16)]: { seat: 1 },
    ["b".repeat(16)]: "a string",
  };
  assert.deepEqual(parseSeats(JSON.stringify(junk)), { [ID]: { seat: 2, token: TOKEN } });
});

test("at most one request's worth of ids is looked up, newest kept", () => {
  const many = Object.fromEntries(Array.from({ length: 60 }, (_, i) => [i.toString(16).padStart(16, "0"), {}]));
  const ids = mineIds(many);
  assert.equal(ids.length, 50);
  assert.equal(ids[49], (59).toString(16).padStart(16, "0"));
});

test("a claim writes the seat in pbp.remember's shape and keeps the rest", () => {
  const other = "f".repeat(16);
  const before = JSON.stringify({ [other]: { seat: 1, token: TOKEN, extra: "kept" } });
  const after = JSON.parse(withSeat(before, ID, 3, TOKEN));
  assert.deepEqual(after[ID], { seat: 3, token: TOKEN });
  assert.deepEqual(after[other], { seat: 1, token: TOKEN, extra: "kept" }, "never pruned or rewritten");
  assert.deepEqual(JSON.parse(withSeat("not json", ID, 1, TOKEN)), { [ID]: { seat: 1, token: TOKEN } });
  assert.deepEqual(JSON.parse(withSeat(null, ID, 1, TOKEN)), { [ID]: { seat: 1, token: TOKEN } });
});

test("opening a match carries the seat's token, and there is no bare link", () => {
  assert.equal(openLink(ID, { seat: 2, token: TOKEN }, "https://g/"), `https://g/#pbp=${ID}:${TOKEN}`);
  assert.equal(openLink(ID, undefined, "https://g/"), null);
  assert.equal(openLink(ID, { seat: 2 }, "https://g/"), null);
});

test("a seat link is the game's own #pbp fragment", () => {
  assert.equal(seatLink(ID, "ab".repeat(16), "https://g/"), `https://g/#pbp=${ID}:${"ab".repeat(16)}`);
});

test("the deadline reads forwards and backwards, and not at all without one", () => {
  const opened = Date.parse(base.turn_opened_at);
  assert.equal(deadlineNote(base, opened + 19 * 3600000), "due in 5h");
  assert.equal(deadlineNote(base, opened + 26 * 3600000), "overdue by 2h");
  assert.equal(deadlineNote({ ...base, deadline_hours: null }, opened), "");
  assert.equal(deadlineLabel(48), "2-day turns");
  assert.equal(deadlineLabel(36), "36h turns");
  assert.equal(deadlineLabel(null), "no deadline");
});

test("the waiting note names who is outstanding and what a lapse does", () => {
  assert.equal(waitingNote({ ...base, names: { 2: "Bob" }, lapsed: { 2: "bot" } }),
               "waiting on Bob (Crimson) · Crimson lapsed (bot plays)");
  assert.equal(waitingNote({ ...base, finished: true }), "");
  assert.equal(waitingNote({ ...base, waiting: [1, 2], unclaimed: [2], names: { 1: "Al" } }),
               "waiting on Al (Azure)", "an open seat is nobody to wait on");
  assert.equal(waitingNote({ ...base, waiting: [2], unclaimed: [2] }), "");
});

test("both halves derive the same pass-phrase", async () => {
  assert.equal(phrase("00112233445566ff"), "amber-boulder-comet");
  assert.equal(phrase("ABCDEF0123456789"), "");
});
