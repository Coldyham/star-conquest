// A gauntlet's graph as the game reads it, and where each stage is drawn: pure,
// no DOM. The board's mirror of `starconquest/gauntlet.py`'s `Gauntlet.from_dict`
// (lanes, start and each stage's `bonus_life`), `depths`, `reachable`,
// `hidden_stages`, `goals`, `grants_life`, `lane_label`, `layout` and `cell_names`, so the page's diagram is the picture the game will
// show. `leaderboard/tests/fixtures/gauntlet-layout.json` is read by both
// suites, so the two cannot drift (docs/design/gauntlet.md, "Lanes on the
// board").

const TARGET_KEYS = ["turns", "lost", "bot", "beat"];

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isInt(value) {
  return typeof value === "number" && Number.isInteger(value);
}

/** A lane's gate as the game's `_target` reads one, or null. */
function gateOf(how) {
  if (!isObject(how)) return null;
  const { turns, lost } = how;
  if (!isInt(turns) || turns < 1) return null;
  if ("lost" in how && (!isInt(lost) || lost < 0)) return null;
  return Object.fromEntries(TARGET_KEYS.filter((k) => how[k] !== undefined).map((k) => [k, how[k]]));
}

const pairKey = (a, b) => `${a},${b}`;

/** The game's MAX_STAGES: a stored `at` is two whole numbers below it. */
const CELLS = 64;

/** A stage's stored `at` as `_cell` reads it, or null. */
export function cellOf(raw) {
  if (!Array.isArray(raw) || raw.length !== 2) return null;
  const [col, row] = raw;
  return isInt(col) && isInt(row) && col >= 0 && row >= 0 && col < CELLS && row < CELLS ? [col, row] : null;
}

/**
 * A body's graph, read tolerantly as `Gauntlet.from_dict` reads it: stages by
 * int id in list order (a duplicate dropped), no `lanes` the list-order chain,
 * a lane to a missing stage or a pair already joined (either way) dropped, no
 * usable `start` the first stage.
 */
export function graphOf(body) {
  const ids = [];
  const bonus = new Map();
  const at = new Map();
  for (const stage of Array.isArray(body?.stages) ? body.stages : []) {
    if (isObject(stage) && isInt(stage.id) && !ids.includes(stage.id)) {
      ids.push(stage.id);
      if (typeof stage.bonus_life === "boolean") bonus.set(stage.id, stage.bonus_life);
      if (cellOf(stage.at)) at.set(stage.id, cellOf(stage.at));
    }
  }
  const have = new Set(ids);
  const lanes = [];
  const twoWay = new Set();
  const gates = new Map();
  const hidden = new Set();
  if (Array.isArray(body?.lanes)) {
    const joined = new Set();
    for (const pair of body.lanes) {
      if (!Array.isArray(pair) || (pair.length !== 2 && pair.length !== 3)) continue;
      const [a, b, how] = pair;
      if (!isInt(a) || !isInt(b) || !have.has(a) || !have.has(b) || a === b) continue;
      if (joined.has(pairKey(a, b)) || joined.has(pairKey(b, a))) continue;
      joined.add(pairKey(a, b));
      lanes.push([a, b]);
      if (pair.length === 3 && isObject(how)) {
        if (how.two_way === true) twoWay.add(pairKey(a, b));
        const gate = gateOf(how);
        if (gate) {
          gates.set(pairKey(a, b), gate);
          if (how.hidden === true) hidden.add(pairKey(a, b));
        }
      }
    }
  } else {
    for (let i = 1; i < ids.length; i++) lanes.push([ids[i - 1], ids[i]]);
  }
  let start = (Array.isArray(body?.start) ? body.start : []).filter((s) => have.has(s));
  start = [...new Set(start)];
  if (!start.length && ids.length) start = [ids[0]];
  return { ids, lanes, twoWay, gates, hidden, start, bonus, at };
}

/** Every way a lane runs, as `{from, to, lane}`; a two-way lane gives two. */
export function ways(graph) {
  const out = [];
  for (const [a, b] of graph.lanes) {
    const lane = pairKey(a, b);
    out.push({ from: a, to: b, lane });
    if (graph.twoWay.has(lane)) out.push({ from: b, to: a, lane });
  }
  return out;
}

function onward(graph, sid, gated = true) {
  const out = new Set();
  for (const w of ways(graph)) {
    if (w.from === sid && (gated || !graph.gates.has(w.lane))) out.add(w.to);
  }
  return out;
}

function walk(graph, gated) {
  const depth = new Map(graph.start.map((s) => [s, 0]));
  const queue = [...graph.start];
  while (queue.length) {
    const at = queue.shift();
    for (const n of [...onward(graph, at, gated)].sort((x, y) => x - y)) {
      if (!depth.has(n)) {
        depth.set(n, depth.get(at) + 1);
        queue.push(n);
      }
    }
  }
  return depth;
}

/** Lanes from the nearest start over ungated lanes only (`Gauntlet.depths`). */
export function depths(graph) {
  return walk(graph, false);
}

/** Every stage some start reaches over any lane (`Gauntlet.reachable`). */
export function reachable(graph) {
  return new Set(walk(graph, true).keys());
}

/** Stages every way into which is a hidden lane, never a start (`hidden_stages`). */
export function hiddenStages(graph) {
  const ins = new Map();
  for (const w of ways(graph)) {
    if (!ins.has(w.to)) ins.set(w.to, []);
    ins.get(w.to).push(graph.hidden.has(w.lane));
  }
  return new Set([...ins].filter(([sid, way]) => way.every(Boolean) && !graph.start.includes(sid)).map(([sid]) => sid));
}

/** The stages a run ends on (`gauntlet.goals`). */
export function goals(graph) {
  const reached = reachable(graph);
  const plainIn = new Set(ways(graph).filter((w) => !graph.gates.has(w.lane)).map((w) => w.to));
  return new Set(graph.ids.filter((sid) => reached.has(sid) && onward(graph, sid, false).size === 0
                                          && (graph.start.includes(sid) || plainIn.has(sid))));
}

/** No lane out at all, gated or not, and not a goal (`gauntlet.dead_end`). */
export function deadEnd(graph, sid) {
  return onward(graph, sid).size === 0 && !goals(graph).has(sid);
}

/** Does winning `sid` in a run grant a life (`gauntlet.grants_life`): the
 * stage's own `bonus_life` where it is ticked either way, else a dead end. */
export function grantsLife(graph, sid) {
  if (!graph.ids.includes(sid)) return false;
  return graph.bonus.has(sid) ? graph.bonus.get(sid) : deadEnd(graph, sid);
}

export const BONUS = "Bonus";
export const SHORTCUT = "Shortcut";
export const GATED = "Gated";

/** What the way from `a` to `b` is called, or null when ungated (`lane_label`). */
export function laneLabel(graph, a, b) {
  const way = ways(graph).find((w) => w.from === a && w.to === b);
  if (!way || !graph.gates.has(way.lane)) return null;
  const ends = goals(graph);
  const seen = new Set([b]);
  const queue = [b];
  let found = false;
  while (queue.length) {
    const at = queue.shift();
    if (ends.has(at)) {
      found = true;
      break;
    }
    for (const n of onward(graph, at)) {
      if (!seen.has(n)) {
        seen.add(n);
        queue.push(n);
      }
    }
  }
  if (!found) return BONUS;
  const depth = depths(graph);
  if (depth.has(a) && depth.has(b) && depth.get(b) > depth.get(a) + 1) return SHORTCUT;
  return GATED;
}

/**
 * Where each stage is drawn, as a Map of stage id to `[column, row]`: the
 * game's `gauntlet.layout`, rule for rule. Column is ungated depth; a stage
 * reached only through gated lanes is one past its shallowest placed source,
 * walked level by level; an unreached stage takes a last column. Row is list
 * order within the column, then one pass sorting each later column by the mean
 * row of its parents in earlier columns (a stage with none by its own row).
 *
 * A graph whose every stage has its own stored `at`, no two alike, is drawn
 * where its author put it instead (`graph.at`).
 */
export function layout(graph) {
  const stored = graph.ids.map((sid) => graph.at?.get(sid));
  if (stored.every(Boolean) && new Set(stored.map(String)).size === stored.length) {
    return new Map(graph.ids.map((sid, i) => [sid, stored[i]]));
  }
  const column = depths(graph);
  const all = ways(graph);
  for (let level = 0; [...column.values()].includes(level); level++) {
    for (const w of all) {
      if (column.get(w.from) === level && !column.has(w.to)) column.set(w.to, level + 1);
    }
  }
  const last = Math.max(...column.values()) + 1;
  for (const sid of graph.ids) if (!column.has(sid)) column.set(sid, last);

  const parents = new Map();
  for (const w of all) {
    if (column.get(w.from) < column.get(w.to)) {
      if (!parents.has(w.to)) parents.set(w.to, new Set());
      parents.get(w.to).add(w.from);
    }
  }
  const row = new Map();
  for (const c of [...new Set(column.values())].sort((x, y) => x - y)) {
    let here = graph.ids.filter((sid) => column.get(sid) === c);
    if (c > 0) {
      const key = (sid, i) => {
        const above = parents.get(sid);
        return above ? [...above].reduce((sum, p) => sum + row.get(p), 0) / above.size : i;
      };
      here = here.map((sid, i) => [key(sid, i), sid]).sort((x, y) => x[0] - y[0]).map(([, sid]) => sid);
    }
    here.forEach((sid, r) => row.set(sid, r));
  }
  return new Map(graph.ids.map((sid) => [sid, [column.get(sid), row.get(sid)]]));
}

/**
 * Each stage's place on the graph, as a node's corner shows it
 * (`gauntlet.cell_names`): "3" for the only stage in column 3, "3-2" for the
 * second row down of a column that holds more. Counted over `shown` (every
 * stage by default), so a stage a player can't see yet takes no row number.
 * An untitled stage is named from these over every stage ("Stage 3-2").
 */
export function cellNames(graph, shown = null) {
  const cells = layout(graph);
  const keep = shown === null ? null : new Set(shown);
  const columns = new Map();
  for (const sid of graph.ids) {
    if (keep && !keep.has(sid)) continue;
    const col = cells.get(sid)[0];
    if (!columns.has(col)) columns.set(col, []);
    columns.get(col).push(sid);
  }
  const out = new Map();
  for (const [col, here] of columns) {
    here.sort((a, b) => cells.get(a)[1] - cells.get(b)[1]);
    here.forEach((sid, r) => out.set(sid, here.length === 1 ? `${col + 1}` : `${col + 1}-${r + 1}`));
  }
  return out;
}
