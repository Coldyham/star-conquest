// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import {
  cleanQuery, filtersFromParams, isFiltered, listQuery, MAX_EXCLUDED, searchClause, urlFor,
} from "../js/listing.mjs";

const parse = (qs) => filtersFromParams(new URLSearchParams(qs));

test("the default view is the plain index, and round-trips to itself", () => {
  const filters = parse("");
  assert.equal(urlFor(filters), "index.html");
  assert.equal(isFiltered(filters), false);
});

test("every filter round-trips through the URL", () => {
  const qs = "bot=marshal&group=config&q=blitz&sort=maps&players=3&mode=symmetric&fog=on" +
    "&contested=1&botlead=1&unplayed=1";
  assert.equal(urlFor(parse(qs)), `index.html?${qs}`);
});

test("unknown values are dropped rather than sent to the database", () => {
  const filters = parse("players=9&mode=hex&fog=maybe&sort=bogus&contested=yes");
  assert.equal(urlFor(filters), "index.html");
});

test("a sort that belongs to the other view falls back to recent", () => {
  assert.equal(parse("sort=new").sort, "new");
  assert.equal(parse("group=config&sort=new").sort, "recent");
  // A config filter forces the per-game view, so its sorts apply.
  assert.equal(parse("group=config&config=abc&sort=new").sort, "new");
});

test("a sort or the grouping alone is not a filter", () => {
  assert.equal(isFiltered(parse("sort=scores&group=config")), false);
  assert.equal(isFiltered(parse("fog=off")), true);
});

test("the per-map filters become server-side conditions", () => {
  const { kind, query } = listQuery(parse("contested=1&botlead=1&fog=off&players=4&mode=random"));
  assert.equal(kind, "game");
  assert.match(query, /^game_summary\?select=[^&]*&score_count=gt\.0/);
  for (const part of ["&contenders=gte.2", "&bot_leads=is.true", "&fog=is.false", "&players=eq.4", "&mode=eq.random"]) {
    assert.ok(query.includes(part), part);
  }
  assert.ok(query.endsWith("&order=last_activity.desc.nullslast,game_key"));
});

test("the grouped view keeps the filters a config has and drops the per-map ones", () => {
  const { kind, query } = listQuery(parse("group=config&contested=1&botlead=1&unplayed=1&fog=on&sort=maps"),
    { played: ["k1"] });
  assert.equal(kind, "config");
  assert.ok(query.startsWith("config_summary?select=*"));
  assert.ok(query.includes("&fog=is.true"));
  for (const absent of ["contenders", "bot_leads", "game_key=not", "score_count=gt"]) {
    assert.ok(!query.includes(absent), absent);
  }
  assert.ok(query.endsWith("&order=game_count.desc,last_activity.desc.nullslast,config_key"));
});

test("unplayed excludes the player's maps by key, capped, and is inert with none", () => {
  const played = Array.from({ length: MAX_EXCLUDED + 5 }, (_, i) => `k${i}`);
  const { query } = listQuery(parse("unplayed=1"), { played });
  const list = decodeURIComponent(query.match(/&game_key=not\.in\.\(([^)]*)\)/)[1]).split(",");
  assert.equal(list.length, MAX_EXCLUDED);
  assert.equal(list[0], '"k0"');
  assert.ok(!listQuery(parse("unplayed=1")).query.includes("game_key="));
});

test("every order ends on the view's own key, so paging never repeats a row", () => {
  for (const sort of ["recent", "scores", "players", "new"]) {
    assert.ok(listQuery(parse(`sort=${sort}`)).query.endsWith(",game_key"), sort);
  }
  for (const sort of ["recent", "scores", "maps"]) {
    assert.ok(listQuery(parse(`group=config&sort=${sort}`)).query.endsWith(",config_key"), sort);
  }
});

test("a search loses PostgREST's own syntax but keeps letters in any script", () => {
  assert.equal(cleanQuery(' "Blitz", (ladder)* '), "Blitz ladder");
  assert.equal(cleanQuery("Zoë.Ståhl"), "Zoë Ståhl");
  assert.equal(cleanQuery("x".repeat(60)).length, 40);
  assert.equal(cleanQuery("..."), "");
});

test("a search matches name, tag and leader, and a bare number the seed too", () => {
  const words = decodeURIComponent(searchClause("Blitz", "game"));
  assert.equal(words, '&or=(config_name.ilike."*Blitz*",config_tags.cs.{"blitz"},best_user_name.ilike."*Blitz*")');
  assert.ok(decodeURIComponent(searchClause("42", "game")).endsWith(",seed.eq.42)"));
  // A config has neither a leader nor a seed.
  assert.equal(decodeURIComponent(searchClause("42", "config")),
    '&or=(config_name.ilike."*42*",config_tags.cs.{"42"})');
  assert.equal(searchClause("", "game"), "");
});
