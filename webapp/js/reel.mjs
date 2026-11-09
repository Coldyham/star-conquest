// A board being walked through a film: the page's mirror of `turnfilm.Reel`.
//
// Events carry results, not rules (`docs/design/turnfilm.md`): every cue only
// assigns what Python computed, so this file decides nothing and draws no dice.
// A fleet is named by its position in the board the film starts from, which is
// why `serial.board` keeps `state.fleets` order and `Reel` indexes it once, up
// front, exactly as Python's does.

/** A `serial.board` snapshot as a mutable board. */
export function fromBoard(snap) {
  return {
    turn: snap.turn,
    winner: snap.winner,
    sys: new Map(snap.sys.map(([id, owner, ships, prog]) => [id, { id, owner, ships, prog }])),
    fleets: snap.fleets.map(([owner, src, dst, ships, total, remaining, slot]) =>
      ({ owner, src, dst, ships, total, remaining, slot })),
    players: new Map(snap.players.map(([id, alive, lost]) => [id, { id, alive, lost }])),
  };
}

/** A mutable board back in `serial.board`'s shape (for comparing). */
export function toBoard(board) {
  return {
    turn: board.turn,
    winner: board.winner,
    sys: [...board.sys.values()].sort((a, b) => a.id - b.id)
      .map((s) => [s.id, s.owner, s.ships, s.prog]),
    fleets: board.fleets.map((f) => [f.owner, f.src, f.dst, f.ships, f.total, f.remaining, f.slot]),
    players: [...board.players.values()].sort((a, b) => a.id - b.id)
      .map((p) => [p.id, p.alive, p.lost]),
  };
}

/** `Fleet.progress_at`: how far along its lane a fleet is part-way through
 * this turn's step (`t` from 0 to 1). The one formula, mirrored. */
export function progressAt(fleet, t) {
  if (fleet.total <= 0) return 1.0;
  return 1.0 - (fleet.remaining + (1.0 - t)) / fleet.total;
}

/** How far through the turn's movement the fleets are at `ms` (`Film.travel`). */
export function travel(film, ms) {
  for (const [kind, , start, len] of film.beats) {
    if (kind === "move" && len > 0 && start <= ms && ms < start + len) {
      return Math.min(1, Math.max(0, (ms - start) / len));
    }
  }
  return 1.0;
}

/** The caption at `ms` (`Film.label`). */
export function label(film, ms) {
  for (const [, text, start, len] of film.beats) {
    if (len > 0 && start <= ms && ms < start + len) return text;
  }
  return "";
}

export class Reel {
  constructor(board, film) {
    this.board = board;
    this.film = film;
    this.at = 0;
    this.byId = new Map(board.fleets.map((f, i) => [i, f]));
  }

  /** Apply every cue due by `ms`, in cue order; answer the events applied. */
  runTo(ms) {
    const applied = [];
    const cues = this.film.cues;
    while (this.at < cues.length && cues[this.at][0] <= ms) {
      const ev = cues[this.at][1];
      this.apply(ev);
      applied.push(ev);
      this.at += 1;
    }
    return applied;
  }

  run() {
    return this.runTo(Infinity);
  }

  get done() {
    return this.at >= this.film.cues.length;
  }

  fleet(id) {
    const f = this.byId.get(id);
    if (f === undefined) throw new Error(`film names fleet ${id}, which this board lacks`);
    return f;
  }

  drop(id) {
    const f = this.fleet(id);
    this.byId.delete(id);
    this.board.fleets = this.board.fleets.filter((g) => g !== f);
  }

  system(id) {
    const s = this.board.sys.get(id);
    if (s === undefined) throw new Error(`film names system ${id}, which this board lacks`);
    return s;
  }

  apply(ev) {
    switch (ev.k) {
      case "Launched": {
        this.system(ev.source_id).ships = ev.source_ships;
        const topped = this.byId.get(ev.fleet);
        if (topped !== undefined) { // a later order down the same lane
          topped.ships = ev.ships;
          return;
        }
        const f = {
          owner: ev.owner_id, src: ev.source_id, dst: ev.dest_id, ships: ev.ships,
          total: ev.turns_total, remaining: ev.turns_remaining, slot: ev.lane_slot,
        };
        this.board.fleets.push(f);
        this.byId.set(ev.fleet, f);
        return;
      }
      case "Advanced":
        for (const [id, remaining] of ev.steps) this.fleet(id).remaining = remaining;
        return;
      case "Clashed":
        if (ev.survivor !== null) this.fleet(ev.survivor).ships = ev.survivors;
        for (const id of ev.dead) this.drop(id);
        return;
      case "Landed": {
        for (const id of ev.fleets) this.drop(id);
        const s = this.system(ev.node_id);
        s.owner = ev.owner_id;
        s.ships = ev.ships;
        s.prog = ev.prod_progress;
        return;
      }
      case "Produced":
        for (const [id, ships, prog] of ev.ticks) {
          const s = this.system(id);
          s.ships = ships;
          s.prog = prog;
        }
        return;
      case "Ended":
        this.board.turn = ev.turn;
        this.board.winner = ev.winner;
        for (const [id, alive] of ev.alive) this.board.players.get(id).alive = alive;
        for (const [id, lost] of ev.ships_lost) this.board.players.get(id).lost = lost;
        return;
      default:
        throw new Error(`not a turn event: ${ev.k}`);
    }
  }
}
