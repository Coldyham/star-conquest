// What a turn's film leaves on the map: a burst and each side's losses where a
// fight happened, and `+N` where a hull was finished (`viewstate.read_marks`,
// `Ui.archive_marks`, `Ui.age_fading_marks`). Read once from the events as they
// fire, then aged on their own clock, so a mark keeps dissolving on top of the
// next turn's glide rather than vanishing with the film that made it.

export const FLASH_MS = 260;   // config.FILM_FLASH_MS: fully visible this long
export const FADE_MS = 500;    // config.FILM_FADE_MS: then dissolves over this

/** `viewstate.read_marks`: the marks `events` earn, for whatever `sees(id)`
 * allows. `owners(id)` gives a hull's owner at the time it was built. */
export function readMarks(events, sees, owners, age = 0) {
  let fights = [];
  let hulls = [];
  for (const ev of events) {
    if (ev.k === "Clashed") {
      if (!sees(ev.low_id) && !sees(ev.high_id)) continue;
      fights.push({ age, node: null, low: ev.low_id, high: ev.high_id, at: ev.at, color: null,
        losses: ev.losses });
    } else if (ev.k === "Landed") {
      // No steps: a reinforcement or an unopposed landing, neither a fight.
      if (!ev.steps.length || !sees(ev.node_id)) continue;
      const color = ev.owner_id === ev.was_owner ? null : ev.owner_id;
      fights = fights.filter((f) => f.node !== ev.node_id);
      fights.push({ age, node: ev.node_id, low: null, high: null, at: 0, color, losses: ev.losses });
    } else if (ev.k === "Produced") {
      for (const [sid, n] of ev.hulls) {
        if (!sees(sid)) continue;   // a rival's yard is not ours to report
        hulls = hulls.filter((h) => h.node !== sid);
        hulls.push({ age, node: sid, hulls: n, owner: owners(sid) });
      }
    }
  }
  return [fights, hulls];
}

/** A burst's pop-in, 0..1 over `FLASH_MS` (`render._mark_phase`). */
export function markPhase(age) {
  return Math.min(1, Math.max(0, age / FLASH_MS));
}

/** How far a mark has dissolved, 0 fresh to 1 gone (`render._mark_fade`). */
export function markFade(age) {
  return age <= FLASH_MS ? 0 : Math.min(1, (age - FLASH_MS) / FADE_MS);
}

export class Marks {
  constructor() {
    this.fights = [];
    this.hulls = [];
  }

  /** Newly applied events into fading marks; a second fight or hull at the
   * same system supersedes the first (`Ui.archive_marks`). */
  archive(board, events, sees) {
    const [fights, hulls] = readMarks(events, sees, (id) => board.sys.get(id).owner);
    const taken = new Set(fights.filter((f) => f.node !== null).map((f) => f.node));
    this.fights = this.fights.filter((f) => !taken.has(f.node)).concat(fights);
    const built = new Set(hulls.map((h) => h.node));
    this.hulls = this.hulls.filter((h) => !built.has(h.node)).concat(hulls);
  }

  /** Advance every mark's clock by `dt` ms and drop the dissolved. */
  age(dt) {
    const life = FLASH_MS + FADE_MS;
    this.fights = this.fights.filter((f) => f.age + dt < life).map((f) => ({ ...f, age: f.age + dt }));
    this.hulls = this.hulls.filter((h) => h.age + dt < life).map((h) => ({ ...h, age: h.age + dt }));
  }

  clear() {
    this.fights = [];
    this.hulls = [];
  }

  get any() {
    return this.fights.length > 0 || this.hulls.length > 0;
  }
}
