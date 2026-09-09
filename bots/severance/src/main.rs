//! severance -- a Star Conquest bot that plays the graph rather than the fight.
//!
//! Speaks the line-oriented JSON protocol in `docs/bot-api.md` on stdin/stdout.
//!
//! The idea, in one line: a system is worth what its loss costs the rival's
//! territory, not what its garrison is worth. Every turn the bot rebuilds the
//! rivals' induced subgraphs, asks of every reachable target how much of their
//! territory removing it separates, and spends the whole turn's spare ships as
//! one pool against the ranked list -- several systems combining on one target
//! when their lanes are the same length, since arrivals on the same turn are
//! resolved together.
//!
//! Purity: the only inputs are the handshake and the turn payload. No clock, no
//! files, no network, no environment. Ties are broken with a hash of the
//! payload's own `rng_seed`, so a run reproduces exactly.

mod graph;
mod json;

use json::J;
use std::collections::HashMap;
use std::io::{self, BufRead, Write};

const NAME: &str = "severance";
const VERSION: &str = "0.1.0";

// --- Fight pricing -------------------------------------------------------
//
// The runner sends `edge_attacking`/`edge_defending` -- the break-even
// multiples against the worst roll -- and the two knobs behind them. The
// dice half of an edge is the edge with the ground taken out of it
// (`edge_attacking / defender_advantage`), and it is floored at the value it
// had when this bot was fitted, so a host lowering the jitter can never lower
// a margin below what was measured.
const TUNED_DICE_EDGE: f64 = 1.2222; // (1+j)/(1-j) at the default 0.10 jitter

/// Attacking a rival: the ground, none of the dice. 86.7% of out-matched
/// garrisons evacuate rather than stand, so a premium against bad dice buys a
/// fight that mostly does not happen. The pad is what is left.
const RIVAL_PAD: f64 = 1.06;

/// Attacking a neutral: the full edge. Nothing decides for a neutral garrison,
/// so it always stands and the dice are always paid -- but it never grows and
/// nothing reinforces it, which is why it gets its own number.
const NEUTRAL_PAD: f64 = 1.0;

/// Our own garrisons cannot decline an engagement, so defence prices the dice
/// in full. This is the asymmetry, and it is deliberate.
const DEFENCE_PAD: f64 = 1.0;

/// What a commit costs in the first pass: the break-even price plus a working
/// margin. Overkill is not paid here. Funding target #1 at 2.4x the price and
/// leaving target #2 unbought loses the expansion race outright -- measured,
/// against marshal -- so the first pass buys as many targets as the turn can
/// afford and the second pass spends what is left.
const COMMIT_MULT: f64 = 1.35;

/// A neutral is bought lean, and no residue is dumped on it. Overkill is
/// cheap against a rival -- the system is contested and will be counted again
/// next turn -- but a neutral garrison neither grows nor counterattacks, so
/// every ship above the price is a ship parked in the wrong system for four
/// turns. Measured: the bot was taking one neutral and then waiting whole
/// turns to afford the next.
const COMMIT_MULT_NEUTRAL: f64 = 1.08;

/// The second pass: every ship still spare is dumped onto a committed target
/// it can reach on the same turn as the rest of the attack. The square law
/// barely punishes overkill and a system taken with two survivors is handed
/// straight back -- so nothing spare stays home, once everything affordable
/// has been bought.
const RESIDUE_DUMP: bool = true;

/// How much a turn of travel discounts a target. Sooner is better at equal
/// value: it is a turn of production earned, and a turn less for the garrison
/// to grow -- but lane lengths swing by more than an order of magnitude across
/// setups, so this is a gentle slope rather than a cutoff.
const ARRIVAL_DISCOUNT: f64 = 0.06;

/// How far above what it holds a front may be saving toward before it stops
/// saving and forwards its surplus. Saving against the rival's capital
/// forever is how a careful bot draws a game it was winning.
const HOARD_REACH: f64 = 2.5;
const HOARD_FLOOR: f64 = 4.0;

/// Ships left on a system that borders a live rival and is under no visible
/// threat. A border with nothing but neutrals behind it is not a border: a
/// neutral never launches anything, so a reserve there is a ship parked.
const FRONT_RESERVE: f64 = 1.0;

// --- What a target is worth ----------------------------------------------
//
// All in the same unit: ships per turn of production. `severance` and the rest
// are scaled to be comparable with it.
const ECON_WEIGHT: f64 = 1.0; // its own output
const SEVER_WEIGHT: f64 = 6.0; // what its loss disconnects for the rival
const JOIN_WEIGHT: f64 = 0.5; // it welds two of my own fragments together
const OPEN_WEIGHT: f64 = 0.12; // lanes it opens onto further territory
const THREAT_WEIGHT: f64 = 0.35; // it is a staging post aimed at me
const NEUTRAL_BIAS: f64 = 1.15; // free real estate is worth a nudge

struct Board {
    n: usize,
    ids: Vec<i64>,
    index: HashMap<i64, usize>,
    production: Vec<f64>,
    rate: Vec<f64>, // ships per turn, i.e. 1 / production
    adj: Vec<Vec<usize>>,
    lane_of: HashMap<(usize, usize), usize>,
    base_turns: Vec<f64>,
}

struct Rules {
    advantage: f64,
    dice_edge: f64, // the dice half, floored at TUNED_DICE_EDGE
    neutral_produces: bool,
}

struct Ctx {
    me: i64,
    board: Board,
    rules: Rules,
}

struct Fleet {
    owner: i64,
    dst: usize,
    ships: f64,
    left: f64,
}

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    let mut ctx: Option<Ctx> = None;
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let msg = match json::parse(&line) {
            Some(m) => m,
            None => continue,
        };
        let reply = match msg.text("type") {
            "hello" => {
                ctx = build_ctx(&msg);
                json::ready_line(NAME, VERSION)
            }
            "turn" => {
                // A turn before a usable handshake is not something to guess
                // at: hold, rather than issue orders against a board we do not
                // have.
                let orders = match ctx.as_ref() {
                    Some(c) => decide(c, &msg),
                    None => Vec::new(),
                };
                json::orders_line(&orders)
            }
            _ => continue,
        };
        if writeln!(stdout, "{}", reply).is_err() || stdout.flush().is_err() {
            break;
        }
    }
}

fn build_ctx(hello: &J) -> Option<Ctx> {
    let me = hello.i("you", 0);
    let map = hello.get("map")?;
    let systems = map.arr("systems");
    let n = systems.len();
    if n == 0 {
        return None;
    }
    let mut ids = Vec::with_capacity(n);
    let mut index = HashMap::with_capacity(n);
    let mut production = vec![1.0; n];
    let mut rate = vec![0.0; n];
    for (i, sys) in systems.iter().enumerate() {
        let id = sys.i("id", i as i64);
        ids.push(id);
        index.insert(id, i);
    }
    let mut adj: graph::Adj = vec![Vec::new(); n];
    for (i, sys) in systems.iter().enumerate() {
        let p = sys.f("production", 1.0).max(1.0);
        production[i] = p;
        rate[i] = 1.0 / p;
        for nb in sys.arr("neighbors") {
            if let Some(&j) = nb.num().and_then(|v| index.get(&(v as i64))) {
                adj[i].push(j);
            }
        }
    }
    let mut lane_of = HashMap::new();
    let mut base_turns = Vec::new();
    for (li, lane) in map.arr("lanes").iter().enumerate() {
        let a = index.get(&lane.i("a", -1)).copied();
        let b = index.get(&lane.i("b", -1)).copied();
        if let (Some(a), Some(b)) = (a, b) {
            lane_of.insert((a, b), li);
            lane_of.insert((b, a), li);
            base_turns.push(lane.f("base_turns", 1.0).max(1.0));
        } else {
            // Keep the lane index aligned with the handshake's order: the
            // per-turn `lane_turns` array is positional against it.
            base_turns.push(1.0);
            let _ = li;
        }
    }
    let rules = hello.get("rules");
    let advantage = rules.map(|r| r.f("defender_advantage", 1.0)).unwrap_or(1.0).max(0.01);
    let edge_attacking = rules.map(|r| r.f("edge_attacking", 1.2222)).unwrap_or(1.2222);
    // The dice half, with the ground divided out, floored at the swing this bot
    // was tuned at. A knob can raise a margin above the measured figure, never
    // lower it.
    let dice_edge = (edge_attacking / advantage).max(TUNED_DICE_EDGE);
    let neutral_produces = rules.map(|r| r.flag("neutral_produces", false)).unwrap_or(false);

    Some(Ctx {
        me,
        board: Board {
            n,
            ids,
            index,
            production,
            rate,
            adj,
            lane_of,
            base_turns,
        },
        rules: Rules {
            advantage,
            dice_edge,
            neutral_produces,
        },
    })
}

/// A deterministic tie-break drawn from the payload's own seed. It shifts
/// nothing that matters -- only which of two equally-ranked targets goes first
/// -- and it is derived, never sampled, so the engine's dice are untouched.
fn mix(seed: u64, salt: u64) -> f64 {
    let mut z = seed
        .wrapping_add(0x9E37_79B9_7F4A_7C15u64.wrapping_mul(salt.wrapping_add(1)));
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^= z >> 31;
    (z >> 11) as f64 / (1u64 << 53) as f64
}

/// A target the turn could not afford, and who was saving toward it.
struct Pending {
    density: f64,
    target: usize,
    short: f64,
    sources: Vec<usize>,
}

struct Candidate {
    target: usize,
    arrival: f64,
    need: f64,
    density: f64,
    sources: Vec<usize>,
}

fn decide(ctx: &Ctx, payload: &J) -> Vec<(i64, i64, i64)> {
    let b = &ctx.board;
    let n = b.n;
    let me = ctx.me;
    let seed = payload.f("rng_seed", 0.0).abs() as u64;

    // --- the board as it stands ------------------------------------------
    let mut owner = vec![0i64; n];
    let mut ships = vec![0.0f64; n];
    let mut progress = vec![0.0f64; n];
    for sys in payload.arr("systems") {
        if let Some(&i) = b.index.get(&sys.i("id", -1)) {
            owner[i] = sys.i("owner", 0);
            ships[i] = sys.f("ships", 0.0).max(0.0);
            progress[i] = sys.f("prod_progress", 0.0);
        }
    }

    // Lane travel times: the handshake's figures unless the payload re-times
    // them, which it does only while ship speed is growing.
    let mut lane_turns = b.base_turns.clone();
    if payload.has("lane_turns") {
        for (li, v) in payload.arr("lane_turns").iter().enumerate() {
            if li < lane_turns.len() {
                if let Some(t) = v.num() {
                    lane_turns[li] = t.max(1.0);
                }
            }
        }
    }
    let cross = |u: usize, v: usize| -> f64 {
        match b.lane_of.get(&(u, v)) {
            Some(&li) => lane_turns[li],
            None => f64::INFINITY,
        }
    };

    let mut fleets: Vec<Fleet> = Vec::new();
    let mut hostile_in = vec![0.0f64; n];
    let mut friendly_in = vec![0.0f64; n];
    for f in payload.arr("fleets") {
        let dst = match b.index.get(&f.i("dst", -1)) {
            Some(&d) => d,
            None => continue,
        };
        let fleet = Fleet {
            owner: f.i("owner", -1),
            dst,
            ships: f.f("ships", 0.0),
            left: f.f("left", 0.0),
        };
        if fleet.owner == me {
            friendly_in[dst] += fleet.ships;
        } else {
            hostile_in[dst] += fleet.ships;
        }
        fleets.push(fleet);
    }

    let mine: Vec<bool> = (0..n).map(|i| owner[i] == me).collect();
    let owned: Vec<usize> = (0..n).filter(|&i| mine[i]).collect();
    if owned.is_empty() {
        return Vec::new(); // eliminated but still being asked: hold.
    }
    let frontier: Vec<usize> = owned
        .iter()
        .copied()
        .filter(|&v| b.adj[v].iter().any(|&w| !mine[w]))
        .collect();
    let is_front: Vec<bool> = {
        let mut f = vec![false; n];
        for &v in &frontier {
            f[v] = true;
        }
        f
    };

    // --- defence, and what that leaves spare ------------------------------
    //
    // Threat is a schedule, not a total. Fleets arrive on the turn they
    // arrive, and each wave is a separate fight -- so a garrison is priced
    // against the waves in order, with the production and the reinforcements
    // that land before each one. Summing every inbound fleet into one number
    // instead locks a whole garrison against a threat six turns away, and the
    // bot never attacks again.
    let defend_edge = ctx.rules.dice_edge * DEFENCE_PAD / ctx.rules.advantage;
    let mut spare = vec![0.0f64; n];
    let mut doomed = vec![false; n];
    for &v in &owned {
        if hostile_in[v] > 0.0 {
            let waves = schedule(&fleets, me, v);
            let keep = hold_required(
                ships[v],
                progress[v],
                b.production[v],
                &waves,
                defend_edge,
                ctx.rules.advantage,
            );
            match keep {
                Some(k) => spare[v] = (ships[v] - k).max(0.0),
                None => {
                    // Cannot be held with everything it has. Get the garrison
                    // out, or spend it on the way out.
                    doomed[v] = true;
                    spare[v] = ships[v];
                }
            }
            continue;
        }
        {
            let exposed = b.adj[v]
                .iter()
                .any(|&w| owner[w] != me && owner[w] != 0);
            let reserve = if exposed { FRONT_RESERVE } else { 0.0 };
            spare[v] = (ships[v] - reserve).max(0.0);
        }
    }

    // --- what every reachable target is worth -----------------------------
    let board_weight: f64 = (0..n).map(|i| b.rate[i]).sum();
    let (my_comp, _) = graph::components(&b.adj, &mine);
    let mut rival_masks: HashMap<i64, Vec<bool>> = HashMap::new();
    for &pid in owner.iter() {
        if pid != me && pid != 0 && !rival_masks.contains_key(&pid) {
            rival_masks.insert(pid, (0..n).map(|i| owner[i] == pid).collect());
        }
    }

    let mut value = vec![0.0f64; n];
    let mut reachable = vec![false; n];
    for &v in &owned {
        for &t in &b.adj[v] {
            if !mine[t] {
                reachable[t] = true;
            }
        }
    }
    for t in 0..n {
        if !reachable[t] {
            continue;
        }
        let mut val = ECON_WEIGHT * b.rate[t];

        // What its loss separates. This is the whole point of the bot: a
        // rival's territory is a graph, and an articulation point of it is
        // worth more than the ships standing on it.
        if owner[t] != 0 {
            if let Some(mask) = rival_masks.get(&owner[t]) {
                let cut = graph::severance(&b.adj, mask, &b.rate, t, board_weight);
                if cfg!(feature = "trace") && cut > 0.0 {
                    eprintln!("   CUT t={} cut={:.4} econ={:.3}", b.ids[t], cut, b.rate[t]);
                }
                val += SEVER_WEIGHT * cut;
            }
        }

        // Lanes onto territory that is not yet mine: taking it keeps the
        // advance open rather than ending in a pocket.
        let opens = b.adj[t].iter().filter(|&&w| !mine[w]).count() as f64;
        val += OPEN_WEIGHT * opens;

        // The mirror of severance, on my own side of the line: a system that
        // welds two of my fragments into one.
        let mut comps: Vec<usize> = b.adj[t]
            .iter()
            .filter(|&&w| mine[w])
            .map(|&w| my_comp[w])
            .collect();
        comps.sort_unstable();
        comps.dedup();
        if comps.len() > 1 {
            val += JOIN_WEIGHT * (comps.len() - 1) as f64;
        }

        // A rival's staging post pointed at me.
        if owner[t] != 0 && owner[t] != me {
            let exposed: f64 = b.adj[t].iter().filter(|&&w| mine[w]).map(|&w| ships[w]).sum();
            val += THREAT_WEIGHT * ships[t] / (1.0 + exposed);
        }

        if owner[t] == 0 {
            val *= NEUTRAL_BIAS;
        }
        value[t] = val.max(0.0);
    }

    // --- candidates: one per (target, arrival turn) -----------------------
    //
    // Sources whose lanes take the same number of turns are grouped, because
    // fleets arriving on the same turn are resolved together -- two systems
    // that cannot each afford a target can afford it between them.
    let mut buckets: HashMap<(usize, u32), Vec<usize>> = HashMap::new();
    for &v in &owned {
        if spare[v] <= 0.0 {
            continue;
        }
        for &t in &b.adj[v] {
            if mine[t] {
                continue;
            }
            let k = cross(v, t);
            if !k.is_finite() {
                continue;
            }
            buckets.entry((t, k.round() as u32)).or_default().push(v);
        }
    }

    let mut candidates: Vec<Candidate> = Vec::new();
    let mut saving = vec![false; n];
    let mut pending: Vec<Pending> = Vec::new();
    for ((t, k), mut sources) in buckets {
        let arrival = k as f64;
        let garrison = project(ctx, &owner, &ships, &progress, &fleets, t, arrival);
        // Ours already on the way, landing on the same turn, fight alongside.
        let joining: f64 = fleets
            .iter()
            .filter(|f| f.dst == t && f.owner == me && (f.left - arrival).abs() < 0.5)
            .map(|f| f.ships)
            .sum();
        let pad = if owner[t] == 0 {
            ctx.rules.dice_edge * NEUTRAL_PAD
        } else {
            RIVAL_PAD // the ground only: rival garrisons mostly run
        };
        let price = (garrison * ctx.rules.advantage * pad).max(garrison + 1.0);
        let need = price - joining;
        if need <= 0.0 {
            continue; // already covered by what is in the air
        }
        sources.sort_unstable();
        // Ships are whole. Price in whole ships from here on, or a target
        // needing 7.3 is bought with 7 -- which is not a capture, and, since
        // nothing about the board then changes, is not a capture next turn
        // either. That deadlock is exactly what this rounding prevents.
        let price = need.ceil();
        let available: f64 = sources.iter().map(|&s| spare[s]).sum();
        if available < price {
            // Not this turn. Remember who was saving toward it, and how far
            // short they are: that is what decides whether their surplus waits
            // here or goes to a front that can spend it.
            if price <= HOARD_REACH * available + HOARD_FLOOR {
                for &s in &sources {
                    saving[s] = true;
                }
            }
            pending.push(Pending {
                density: value[t] / (price + 1.0),
                target: t,
                short: price - available,
                sources,
            });
            continue;
        }
        // Sooner is better at equal value: it is a turn of production, and a
        // turn less for the garrison to grow.
        let density = value[t] / (price + 1.0) / (1.0 + ARRIVAL_DISCOUNT * arrival)
            + 1e-9 * mix(seed, (t as u64) << 8 | k as u64);
        candidates.push(Candidate {
            target: t,
            arrival,
            need: price,
            density,
            sources,
        });
    }
    // Best first, so a top-up never drains a front that wants the ships more.
    pending.sort_by(|a, b| {
        b.density
            .partial_cmp(&a.density)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.target.cmp(&b.target))
    });
    // Nothing within saving distance anywhere: the army still has somewhere to
    // be, which is the best target it cannot yet afford.
    if !saving.iter().any(|&x| x) {
        if let Some(p) = pending.first() {
            for &s in &p.sources {
                saving[s] = true;
            }
        }
    }
    // What each system is itself holding out for, so a top-up can tell whose
    // need is greater.
    let mut front_value = vec![0.0f64; n];
    for p in &pending {
        for &s in &p.sources {
            if p.density > front_value[s] {
                front_value[s] = p.density;
            }
        }
    }

    candidates.sort_by(|a, b| {
        b.density
            .partial_cmp(&a.density)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.arrival.partial_cmp(&b.arrival).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.target.cmp(&b.target))
    });

    // --- spend the turn as one pool ---------------------------------------
    //
    // First pass: buy every target the turn can afford, best value per ship
    // first. Second pass: dump the remainder on whatever was bought.
    let mut orders: Vec<(i64, i64, i64)> = Vec::new();
    let mut order_at: HashMap<(usize, usize), usize> = HashMap::new();
    let mut taken = vec![false; n];
    let mut committed: Vec<(usize, f64, f64)> = Vec::new(); // target, arrival, value
    for cand in &candidates {
        if taken[cand.target] {
            continue;
        }
        let available: f64 = cand.sources.iter().map(|&s| spare[s]).sum();
        if available + 1e-9 < cand.need {
            continue; // an earlier, better target spent the ships
        }
        let mult = if owner[cand.target] == 0 {
            COMMIT_MULT_NEUTRAL
        } else {
            COMMIT_MULT
        };
        let want = (cand.need * mult).max(cand.need).min(available).ceil().min(available);
        let mut left = want;
        // Biggest garrisons first: fewer, fatter fleets, and the leftovers
        // stay concentrated on the systems that had least to give.
        let mut order_src = cand.sources.clone();
        order_src.sort_by(|&x, &y| {
            spare[y]
                .partial_cmp(&spare[x])
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| x.cmp(&y))
        });
        let mut sent_total = 0.0;
        let mut parts: Vec<(usize, f64)> = Vec::new();
        for s in order_src {
            if left <= 0.0 {
                break;
            }
            let send = spare[s].min(left).floor();
            if send < 1.0 {
                continue;
            }
            parts.push((s, send));
            left -= send;
            sent_total += send;
        }
        if sent_total + 1e-9 < cand.need {
            continue; // rounding ate it; leave the ships for something else
        }
        for (s, send) in parts {
            spare[s] -= send;
            order_at.insert((s, cand.target), orders.len());
            orders.push((b.ids[s], b.ids[cand.target], send as i64));
        }
        taken[cand.target] = true;
        if owner[cand.target] != 0 {
            committed.push((cand.target, cand.arrival, value[cand.target]));
        }
    }

    // Second pass. A ship may only join an attack it lands with: arrivals are
    // grouped and resolved together, so a fleet one turn late fights alone and
    // dies alone. Hence the arrival-time match rather than mere adjacency.
    if RESIDUE_DUMP {
        for &v in &owned {
            if doomed[v] || spare[v] < 1.0 {
                continue;
            }
            let mut best: Option<(f64, usize)> = None;
            for &(t, arrival, val) in &committed {
                if !b.adj[v].contains(&t) {
                    continue;
                }
                if (cross(v, t) - arrival).abs() > 0.5 {
                    continue;
                }
                if best.map_or(true, |(bv, _)| val > bv) {
                    best = Some((val, t));
                }
            }
            if let Some((_, t)) = best {
                let send = spare[v].floor();
                spare[v] -= send;
                match order_at.get(&(v, t)) {
                    Some(&i) => orders[i].2 += send as i64,
                    None => {
                        order_at.insert((v, t), orders.len());
                        orders.push((b.ids[v], b.ids[t], send as i64));
                    }
                }
            }
        }
    }

    // --- everything still holding a surplus --------------------------------
    //
    // Rear systems stream forward; a doomed one leaves rather than dying with
    // its ships aboard. A frontier system that could not afford its target
    // keeps its surplus: that is how the next turn affords it.
    // Ships go to a front that is short of a capture, not merely to the
    // nearest front. A frontier system with nothing left to buy is rear.
    let mut seeds: Vec<usize> = frontier
        .iter()
        .copied()
        .filter(|&v| !doomed[v] && saving[v])
        .collect();
    if seeds.is_empty() {
        seeds = frontier.iter().copied().filter(|&v| !doomed[v]).collect();
    }
    let (dist, hop) = graph::flow_field(&b.adj, &mine, &seeds, &cross);
    let _ = dist;
    for &v in &owned {
        let send = spare[v].floor();
        if send < 1.0 {
            continue;
        }
        if doomed[v] {
            // Out to the best neighbour that is still ours and still standing.
            let mut best: Option<(f64, usize)> = None;
            for &w in &b.adj[v] {
                if !mine[w] || doomed[w] {
                    continue;
                }
                let score = b.rate[w] + if is_front[w] { 0.5 } else { 0.0 }
                    - 0.01 * cross(v, w);
                if best.map_or(true, |(s, _)| score > s) {
                    best = Some((score, w));
                }
            }
            if let Some((_, w)) = best {
                spare[v] = 0.0;
                orders.push((b.ids[v], b.ids[w], send as i64));
            }
            continue;
        }
        if saving[v] {
            continue; // it is a capture away: build up in place
        }
        if let Some(next) = hop[v] {
            spare[v] = 0.0;
            orders.push((b.ids[v], b.ids[next], send as i64));
        }
    }

    if cfg!(feature = "trace") {
        let owned_ships: f64 = owned.iter().map(|&v| ships[v]).sum();
        let free: f64 = owned.iter().map(|&v| spare[v]).sum();
        eprintln!(
            "T{} sys={} ships={:.0} cands={} committed={} orders={} unspent={:.0} doomed={}",
            payload.i("turn", -1),
            owned.len(),
            owned_ships,
            candidates.len(),
            committed.len(),
            orders.len(),
            free,
            doomed.iter().filter(|&&d| d).count(),
        );
        for p in pending.iter().take(4) {
            eprintln!(
                "   pend t={} own={} ships={:.0} dens={:.3} short={:.0} srcs={:?}",
                b.ids[p.target], owner[p.target], ships[p.target], p.density, p.short,
                p.sources.iter().map(|&x| b.ids[x]).collect::<Vec<_>>()
            );
        }
        for c in candidates.iter().take(4) {
            eprintln!(
                "   cand t={} own={} ships={:.0} need={:.1} val={:.2} dens={:.3} k={} srcs={}",
                b.ids[c.target], owner[c.target], ships[c.target], c.need,
                value[c.target], c.density, c.arrival, c.sources.len()
            );
        }
    }
    orders
}

/// Inbound fleets at one system, folded into per-turn waves: hostile and
/// friendly totals for each turn something lands, in arrival order.
fn schedule(fleets: &[Fleet], me: i64, at: usize) -> Vec<(f64, f64, f64)> {
    let mut waves: Vec<(f64, f64, f64)> = Vec::new();
    for f in fleets.iter().filter(|f| f.dst == at) {
        let k = f.left.max(0.0).round();
        let slot = match waves.iter().position(|w| w.0 == k) {
            Some(i) => i,
            None => {
                waves.push((k, 0.0, 0.0));
                waves.len() - 1
            }
        };
        if f.owner == me {
            waves[slot].2 += f.ships;
        } else {
            waves[slot].1 += f.ships;
        }
    }
    waves.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    waves
}

/// The fewest ships that must stay to survive every inbound wave, or `None`
/// if the whole garrison is not enough.
///
/// Monotone in the number kept, so a binary search settles it. Survivors come
/// from the square law -- a fight leaves `sqrt(mine^2 - theirs^2)` behind, in
/// effective strength -- because a garrison that barely survives wave one is
/// not the garrison that meets wave two.
fn hold_required(
    garrison: f64,
    progress: f64,
    production: f64,
    waves: &[(f64, f64, f64)],
    defend_edge: f64,
    advantage: f64,
) -> Option<f64> {
    let survives = |keep: f64| -> bool {
        let mut strength = keep;
        let p = production.max(1.0);
        let mut prev = 0.0f64;
        for &(k, hostile, friendly) in waves {
            strength += ((progress + k) / p).floor() - ((progress + prev) / p).floor();
            strength += friendly;
            prev = k;
            if hostile <= 0.0 {
                continue;
            }
            if strength < hostile * defend_edge {
                return false;
            }
            let mine = strength * advantage;
            strength = (mine * mine - hostile * hostile).max(0.0).sqrt() / advantage;
        }
        true
    };
    if !survives(garrison) {
        return None;
    }
    let (mut lo, mut hi) = (0.0f64, garrison);
    while hi - lo > 0.5 {
        let mid = ((lo + hi) / 2.0).ceil();
        if mid >= hi {
            break;
        }
        if survives(mid) {
            hi = mid;
        } else {
            lo = mid;
        }
    }
    Some(hi)
}

/// The garrison this target will hold when a fleet launched now arrives.
///
/// Production keeps ticking while ships are in transit and reinforcements
/// already in the air land first, so the count on the board today is the wrong
/// number to price against -- badly wrong on a long lane.
fn project(
    ctx: &Ctx,
    owner: &[i64],
    ships: &[f64],
    progress: &[f64],
    fleets: &[Fleet],
    t: usize,
    arrival: f64,
) -> f64 {
    let holder = owner[t];
    let mut garrison = ships[t];
    if holder != 0 || ctx.rules.neutral_produces {
        let p = ctx.board.production[t].max(1.0);
        garrison += ((progress[t] + arrival) / p).floor();
    }
    for f in fleets {
        if f.dst == t && f.owner == holder && f.left <= arrival + 0.5 {
            garrison += f.ships;
        }
    }
    // A third party attacking the same system may well thin it for us. That is
    // not counted: over-estimating a garrison costs ships, under-estimating
    // costs the system.
    garrison.max(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Four systems in a line: 0 - 1 - 2 - 3. Seat 1 holds 0, seat 2 holds the
    /// rest, so 2 is the node whose loss cuts the rival in two.
    fn line_hello() -> String {
        r#"{"type":"hello","protocol":1,"you":1,"seats":[1,2],
            "setup":{},"budget_ms":150,"reveal_opponents":false,
            "rules":{"combat_jitter":0.1,"defender_advantage":1.0,"swing":1.2222,
                     "edge_attacking":1.2222,"edge_defending":1.2222,
                     "in_lane_battles":false,"neutral_produces":false,
                     "ship_ly_per_turn":6,"ship_speed_growth_pct":0},
            "map":{"systems":[
                {"id":0,"pos":[0,0],"production":5,"neighbors":[1]},
                {"id":1,"pos":[1,0],"production":5,"neighbors":[0,2]},
                {"id":2,"pos":[2,0],"production":5,"neighbors":[1,3]},
                {"id":3,"pos":[3,0],"production":5,"neighbors":[2]}],
              "lanes":[{"a":0,"b":1,"length_ly":6,"base_turns":1},
                       {"a":1,"b":2,"length_ly":6,"base_turns":1},
                       {"a":2,"b":3,"length_ly":6,"base_turns":1}]}}"#
            .to_string()
    }

    fn turn_payload(systems: &str, fleets: &str) -> J {
        json::parse(&format!(
            r#"{{"type":"turn","turn":3,"rng_seed":99,"budget_ms":150,
                 "systems":[{}],"fleets":[{}],
                 "players":[{{"id":1,"alive":true,"ships_lost":0}},
                            {{"id":2,"alive":true,"ships_lost":0}}]}}"#,
            systems, fleets
        ))
        .expect("payload parses")
    }

    #[test]
    fn json_reads_the_shapes_the_protocol_uses() {
        let v = json::parse(r#"{"a":[1,-2.5,1e3],"b":"x\"y\n","c":true,"d":null}"#).unwrap();
        assert_eq!(v.arr("a").len(), 3);
        assert_eq!(v.arr("a")[1].num(), Some(-2.5));
        assert_eq!(v.text("b"), "x\"y\n");
        assert!(v.flag("c", false));
        // Absent and null are both "not sent": lane_turns arrives only while
        // ship speed is growing.
        assert!(!v.has("d"));
        assert!(!v.has("missing"));
        assert_eq!(v.f("missing", 7.0), 7.0);
    }

    #[test]
    fn json_survives_a_star_name() {
        let v = json::parse(r#"{"name":"α Cen 🚀"}"#).unwrap();
        assert_eq!(v.text("name"), "\u{3b1} Cen \u{1f680}");
    }

    #[test]
    fn a_middle_node_cuts_and_an_end_node_does_not() {
        let adj: graph::Adj = vec![vec![1], vec![0, 2], vec![1, 3], vec![2]];
        let rival = [false, true, true, true];
        let weight = [0.2; 4];
        // Removing 2 leaves {1} and {3}: a genuine cut.
        assert!(graph::severance(&adj, &rival, &weight, 2, 0.8) > 0.0);
        // Removing 3 leaves {1,2} in one piece: nothing separated.
        assert_eq!(graph::severance(&adj, &rival, &weight, 3, 0.8), 0.0);
    }

    #[test]
    fn flow_field_points_at_the_nearest_seed_by_travel_time() {
        let adj: graph::Adj = vec![vec![1], vec![0, 2], vec![1, 3], vec![2]];
        let all = [true; 4];
        // The 2-3 lane is long, so 2 should still route back through 1.
        let cost = |a: usize, b: usize| if (a.max(b)) == 3 { 9.0 } else { 1.0 };
        let (dist, hop) = graph::flow_field(&adj, &all, &[0], &cost);
        assert_eq!(hop[3], Some(2));
        assert_eq!(hop[2], Some(1));
        assert!(dist[3] > dist[2]);
    }

    #[test]
    fn a_garrison_keeps_only_what_the_waves_require() {
        // 10 hostile landing in 2 turns, no production, no advantage.
        let waves = [(2.0, 10.0, 0.0)];
        let keep = hold_required(30.0, 0.0, 1e9, &waves, 1.0, 1.0).expect("holdable");
        assert!(keep >= 10.0 && keep <= 12.0, "kept {}", keep);
        // The rest is spare -- the whole point: a threat two turns out must not
        // freeze a thirty-ship garrison.
        assert!(30.0 - keep >= 18.0);
        // Not holdable with everything.
        assert!(hold_required(5.0, 0.0, 1e9, &waves, 1.0, 1.0).is_none());
    }

    #[test]
    fn a_distant_wave_is_met_by_production_rather_than_by_hoarding() {
        // 6 hostile landing in 10 turns, one ship produced every 2 turns.
        let waves = [(10.0, 6.0, 0.0)];
        let keep = hold_required(8.0, 0.0, 2.0, &waves, 1.0, 1.0).expect("holdable");
        assert!(keep <= 2.0, "kept {} when production covers the wave", keep);
    }

    #[test]
    fn orders_are_legal_and_affordable() {
        let ctx = build_ctx(&json::parse(&line_hello()).unwrap()).unwrap();
        let payload = turn_payload(
            r#"{"id":0,"owner":1,"ships":40,"prod_progress":0},
               {"id":1,"owner":2,"ships":3,"prod_progress":0},
               {"id":2,"owner":2,"ships":3,"prod_progress":0},
               {"id":3,"owner":2,"ships":3,"prod_progress":0}"#,
            "",
        );
        let orders = decide(&ctx, &payload);
        assert!(!orders.is_empty(), "40 ships against a 3-ship neighbour");
        let mut spent = 0;
        for (src, dst, ships) in &orders {
            assert_eq!(*src, 0, "we only hold system 0");
            assert_eq!(*dst, 1, "system 1 is our only lane");
            assert!(*ships > 0);
            spent += ships;
        }
        assert!(spent <= 40, "spent {} of 40", spent);
    }

    #[test]
    fn the_empty_boards_do_not_panic() {
        let ctx = build_ctx(&json::parse(&line_hello()).unwrap()).unwrap();
        // A system we hold with nothing on it, and every neighbour ours.
        let all_mine = turn_payload(
            r#"{"id":0,"owner":1,"ships":0,"prod_progress":0},
               {"id":1,"owner":1,"ships":0,"prod_progress":0},
               {"id":2,"owner":1,"ships":5,"prod_progress":0},
               {"id":3,"owner":1,"ships":5,"prod_progress":0}"#,
            "",
        );
        assert!(decide(&ctx, &all_mine).is_empty());
        // Eliminated: still asked, owns nothing.
        let landless = turn_payload(
            r#"{"id":0,"owner":2,"ships":1,"prod_progress":0},
               {"id":1,"owner":2,"ships":1,"prod_progress":0},
               {"id":2,"owner":2,"ships":1,"prod_progress":0},
               {"id":3,"owner":2,"ships":1,"prod_progress":0}"#,
            "",
        );
        assert!(decide(&ctx, &landless).is_empty());
        // A turn that arrives before any usable handshake, and a junk line.
        assert!(build_ctx(&json::parse(r#"{"type":"hello"}"#).unwrap()).is_none());
        assert!(json::parse("not json").is_none());
    }

    #[test]
    fn a_doomed_garrison_leaves_rather_than_dying_in_place() {
        let ctx = build_ctx(&json::parse(&line_hello()).unwrap()).unwrap();
        // We hold 0 and 1. 40 hostile land on 1 next turn: it cannot be held,
        // so its ships should fall back to 0 rather than be fed to the fight.
        let payload = turn_payload(
            r#"{"id":0,"owner":1,"ships":2,"prod_progress":0},
               {"id":1,"owner":1,"ships":9,"prod_progress":0},
               {"id":2,"owner":2,"ships":40,"prod_progress":0},
               {"id":3,"owner":2,"ships":5,"prod_progress":0}"#,
            r#"{"owner":2,"src":2,"dst":1,"ships":40,"left":1,"total":1}"#,
        );
        let orders = decide(&ctx, &payload);
        let out: i64 = orders
            .iter()
            .filter(|(src, dst, _)| *src == 1 && *dst == 0)
            .map(|(_, _, n)| n)
            .sum();
        assert!(out > 0, "expected an evacuation, got {:?}", orders);
        assert!(
            !orders.iter().any(|(src, dst, _)| *src == 0 && *dst == 1),
            "nothing should be fed into a system that cannot be held: {:?}",
            orders
        );
    }
}
