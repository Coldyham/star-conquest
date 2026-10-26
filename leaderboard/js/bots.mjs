// What a seat's bot is called on the board: its family, and the stop of the
// family's knob it plays at. The database names a game's opponents the same way
// (`sc_bot_profile` and `sc_bots` in schema.sql); this is the mirror for what
// the page reads straight off a setup (a play-by-post roster, a setup diff).
// The tables come from the game (`bot-stops.mjs`, generated).

import { BOT_KNOBS, BOT_LEGACY, DEFAULT_STOP } from "./bot-stops.mjs";

/**
 * "knower:3" for a seat stored as `name` at `aux`, or the name alone for a bot
 * with no named stops. A name folded into a family (`BOT_LEGACY`: council,
 * claudebot) is that family at its stop; council read any aux rounding to 1 or
 * more as On. A stop is the whole part of aux, clamped into range, except that
 * a value past the top reads as the knob's `over` stop; a missing or
 * unreadable aux is the default stop.
 */
export function seatProfile(name, aux) {
  const raw = aux === null || aux === undefined || aux === "" ? NaN : Number(aux);
  const value = Number.isFinite(raw) ? raw : DEFAULT_STOP;
  const old = BOT_LEGACY[name];
  const family = old ? old.family : name;
  const knob = BOT_KNOBS[family];
  if (!knob) return family;
  let stop;
  if (old) stop = Math.round(value) >= 1 ? old.on : old.off;
  else if (value > knob.hi && knob.over !== null) stop = knob.over;
  else stop = Math.max(knob.lo, Math.min(knob.hi, Math.floor(value)));
  return `${family}:${stop}`;
}

/**
 * The profiles in a `bots` column (`sc_bots`), which holds each seat's family
 * and, for a bot with stops, its "family:stop" too so a filter on either
 * matches: every "family:stop", and every bare name that has none.
 */
export function profiles(bots) {
  const list = [...new Set(bots || [])];
  const staged = new Set(list.filter((bot) => bot.includes(":")).map((bot) => bot.split(":")[0]));
  return list.filter((bot) => bot.includes(":") || !staged.has(bot)).sort();
}

/** The stop's name in a profile, or null at the default stop or for a bare name. */
export function stopName(profile) {
  const [family, stop] = String(profile).split(":");
  const knob = BOT_KNOBS[family];
  if (stop === undefined || !knob || Number(stop) === DEFAULT_STOP) return null;
  return knob.names[Number(stop) - knob.lo] || null;
}

/** "knower · council", or just "knower" at its default stop. */
export function profileLabel(profile) {
  const family = String(profile).split(":")[0];
  const stop = stopName(profile);
  return stop ? `${family} · ${stop.toLowerCase()}` : family;
}
