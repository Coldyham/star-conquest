//! Graph queries over the star map.
//!
//! Everything here works on dense node indices (0..n) rather than system ids,
//! and takes a membership mask so the same routines answer "the enemy's
//! territory", "my territory" or "the whole board" without a second copy of
//! the adjacency.

use std::collections::BinaryHeap;

pub type Adj = Vec<Vec<usize>>;

/// Component id per node, and the number of components, over the subgraph
/// induced by `member`. Non-members get `usize::MAX`.
pub fn components(adj: &Adj, member: &[bool]) -> (Vec<usize>, usize) {
    let n = adj.len();
    let mut comp = vec![usize::MAX; n];
    let mut count = 0usize;
    let mut stack = Vec::new();
    for start in 0..n {
        if !member[start] || comp[start] != usize::MAX {
            continue;
        }
        comp[start] = count;
        stack.push(start);
        while let Some(v) = stack.pop() {
            for &w in &adj[v] {
                if member[w] && comp[w] == usize::MAX {
                    comp[w] = count;
                    stack.push(w);
                }
            }
        }
        count += 1;
    }
    (comp, count)
}

/// Weighted fragmentation caused by removing `cut` from the subgraph induced
/// by `member`.
///
/// Returns the weight of every pair of member nodes that removing `cut`
/// separates, as a fraction of `scale` squared -- so it is zero for a node
/// that is not an articulation point of its component, largest for an even
/// split of a large territory, and comparable between rivals because `scale`
/// is the whole board's weight rather than this rival's.
///
/// This is recomputed per candidate every turn rather than cached: an
/// articulation point is a property of the current ownership, and ownership is
/// exactly what changed since the last call.
pub fn severance(adj: &Adj, member: &[bool], weight: &[f64], cut: usize, scale: f64) -> f64 {
    if scale <= 0.0 {
        return 0.0;
    }
    let n = adj.len();
    let mut seen = vec![false; n];
    seen[cut] = true;
    let mut total = 0.0f64;
    let mut sum_sq = 0.0f64;
    let mut stack = Vec::new();
    for start in 0..n {
        if !member[start] || seen[start] {
            continue;
        }
        // One component of what is left behind.
        let mut w = 0.0f64;
        seen[start] = true;
        stack.push(start);
        while let Some(v) = stack.pop() {
            w += weight[v];
            for &u in &adj[v] {
                if member[u] && !seen[u] {
                    seen[u] = true;
                    stack.push(u);
                }
            }
        }
        total += w;
        sum_sq += w * w;
    }
    let split = total * total - sum_sq;
    if split <= 0.0 {
        0.0
    } else {
        split / (scale * scale)
    }
}

/// Multi-source Dijkstra outward from `seeds`, expanding only through
/// `allowed`, with lane travel time as the edge cost.
///
/// Returns (distance in turns, next hop toward the nearest seed). Seeds
/// themselves need not be `allowed` -- that is what lets a field be aimed at
/// the far side of a front while only ever routing through our own systems.
/// Measuring in travel turns rather than hops is the same rule the rest of the
/// game follows: a lane's length is a setup-dependent thing.
pub fn flow_field(
    adj: &Adj,
    allowed: &[bool],
    seeds: &[usize],
    cost: &dyn Fn(usize, usize) -> f64,
) -> (Vec<f64>, Vec<Option<usize>>) {
    let n = adj.len();
    let mut dist = vec![f64::INFINITY; n];
    let mut hop: Vec<Option<usize>> = vec![None; n];
    let mut heap: BinaryHeap<Step> = BinaryHeap::new();
    for &s in seeds {
        if s < n && dist[s] > 0.0 {
            dist[s] = 0.0;
            heap.push(Step { at: s, d: 0.0 });
        }
    }
    while let Some(Step { at, d }) = heap.pop() {
        if d > dist[at] + 1e-9 {
            continue;
        }
        for &w in &adj[at] {
            if !allowed[w] {
                continue;
            }
            let next = d + cost(w, at);
            if next + 1e-9 < dist[w] {
                dist[w] = next;
                hop[w] = Some(at);
                heap.push(Step { at: w, d: next });
            }
        }
    }
    (dist, hop)
}

struct Step {
    at: usize,
    d: f64,
}

impl PartialEq for Step {
    fn eq(&self, other: &Self) -> bool {
        self.d == other.d && self.at == other.at
    }
}
impl Eq for Step {}
impl Ord for Step {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // Reversed: BinaryHeap is a max-heap and Dijkstra wants the smallest.
        // Ties fall back to the index so the order is total and deterministic.
        other
            .d
            .partial_cmp(&self.d)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| other.at.cmp(&self.at))
    }
}
impl PartialOrd for Step {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}
