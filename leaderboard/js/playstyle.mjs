// The Playstyle panel's arithmetic: pool a player's readings, then summarise.
//
// A reading is one posted game read off its replay by the worker
// (tools/playstyle.py, which documents each field). Every field sums across
// games, so a player's record is their readings added together, and the roster
// column (playstyle-baseline.mjs) is one such record already pooled.

/** The reading layout this page understands (`playstyle.SHAPE`). */
export const SHAPE = 1;
/** Fewer posted games than this and a name gets no column of figures. */
export const MIN_GAMES = 3;
export const THRESHOLDS = [0.5, 0.6, 0.67, 0.75, 0.9];
export const COVER = ["inbound", "reachable", "short", "uncovered"];
const MEASURES = ["income", "ships", "board"];

const sum = (a, b) => a.map((x, i) => x + b[i]);

/** Readings added together; null when none is of a shape this page reads. */
export function pool(records) {
  let out = null;
  for (const r of records) {
    if (!r || r.shape !== SHAPE) continue;
    if (!out) {
      out = structuredClone(r);
      continue;
    }
    out.games += r.games;
    out.turns = out.turns.concat(r.turns);
    out.hand_turns += r.hand_turns;
    out.waves = out.waves.map((row, band) => sum(row, r.waves[band]));
    for (const how of COVER) out.relief[how] = sum(out.relief[how], r.relief[how]);
    out.frontier = sum(out.frontier, r.frontier);
    for (const key of MEASURES) {
      out.reach[key] = out.reach[key].map((xs, i) => xs.concat(r.reach[key][i]));
      out.never[key] = sum(out.never[key], r.never[key]);
    }
  }
  return out;
}

export function median(xs) {
  if (!xs.length) return null;
  const sorted = [...xs].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * The median wave of a ratio histogram (bins in tenths, the last one open), as
 * { ratio, open, count }: `ratio` is the bin's middle, `open` says it is the
 * last bin, so "this or more".
 */
export function waveMedian(bins) {
  const count = bins.reduce((a, b) => a + b, 0);
  if (!count) return null;
  let seen = 0;
  for (let i = 0; i < bins.length; i += 1) {
    seen += bins[i];
    if (seen * 2 >= count) {
      const open = i === bins.length - 1;
      return { ratio: open ? i / 10 : (i + 0.5) / 10, open, count };
    }
  }
  return null;
}

/** What the panel shows for one pooled record. */
export function summary(record) {
  if (!record) return null;
  const all = record.waves[0].map((_, i) => record.waves.reduce((a, row) => a + row[i], 0));
  const empties = COVER.reduce((a, how) => a + record.relief[how][0], 0);
  const lostAfter = COVER.reduce((a, how) => a + record.relief[how][1], 0);
  const covered = record.relief.inbound[0] + record.relief.reachable[0];
  const [held, lost] = record.frontier;
  const reach = (key, x) => {
    const i = THRESHOLDS.indexOf(x);
    return { at: median(record.reach[key][i]), never: record.never[key][i] };
  };
  return {
    games: record.games,
    turns: median(record.turns),
    wave: waveMedian(all),
    waveBy: record.waves.map(waveMedian),
    twoThirdsIncome: reach("income", 0.67),
    twoThirdsShips: reach("ships", 0.67),
    emptiesPerGame: record.games ? empties / record.games : null,
    covered: empties ? covered / empties : null,
    lostAfterEmpty: empties ? lostAfter / empties : null,
    frontierLoss: held ? (lost / held) * 100 : null,
  };
}

const pct = (x) => (x === null ? "—" : `${Math.round(x * 100)}%`);
const times = (w) => (w ? `${w.ratio.toFixed(1)}×${w.open ? "+" : ""}` : "—");
const reached = (r, games) =>
  r.at === null ? "never" : `${pct(r.at)} in` + (r.never ? ` (${r.never}/${games} never)` : "");

/** The panel's rows: a label, a one-line explanation, and how to show a summary. */
export const ROWS = [
  {
    label: "Games read",
    hint: "Posted wins whose replay was read, with at least 10 turns played by hand",
    show: (s) => String(s.games),
  },
  {
    label: "Attack margin",
    hint: "Median force sent at a system you don't hold, over its garrison on arrival (defender's edge counted)",
    show: (s) => times(s.wave),
  },
  {
    label: "…when behind",
    hint: "The same, while holding under half the players' ships",
    show: (s) => times(s.waveBy[0]),
  },
  {
    label: "…when ahead",
    hint: "The same, while holding two-thirds or more of the players' ships",
    show: (s) => times(s.waveBy[2]),
  },
  {
    label: "Two-thirds of the income",
    hint: "How far through the game you first held two-thirds of the players' income",
    show: (s) => reached(s.twoThirdsIncome, s.games),
  },
  {
    label: "Two-thirds of the ships",
    hint: "How far through the game you first held two-thirds of the players' ships",
    show: (s) => reached(s.twoThirdsShips, s.games),
  },
  {
    label: "Frontier empties",
    hint: "Times per game a border system sent 90%+ of its garrison away, with no bigger attack already on its way in",
    show: (s) => (s.emptiesPerGame === null ? "—" : s.emptiesPerGame.toFixed(1)),
  },
  {
    label: "…covered",
    hint: "Of those, how often enough of your ships could get back before the nearest enemy",
    show: (s) => pct(s.covered),
  },
  {
    label: "…then lost",
    hint: "Of those, how often the system fell within five turns",
    show: (s) => pct(s.lostAfterEmpty),
  },
  {
    label: "Border losses",
    hint: "Border systems lost per 100 turns a border system was held",
    show: (s) => (s.frontierLoss === null ? "—" : s.frontierLoss.toFixed(1)),
  },
];
