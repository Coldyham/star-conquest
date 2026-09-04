// Reads a stored setup (games.settings_json / game_summary) into display text:
// what makes this config different from the defaults, and the one-line label a
// badge shows when nobody has named it yet.
//
// No DOM and no fetch in here, so tests/ can exercise it directly — the same
// reason standings.mjs and the pure half of format.mjs exist. This module reads
// data already decoded and stored, so unlike token-decode.mjs it needs no
// inflate and no base64.

// Field -> its short label on a config badge. Settings.token_dict() prunes
// settings_json to non-defaults (starconquest/settings.py), so whatever is
// left over *is* the diff — nothing here needs to know the defaults
// themselves, only how to name each field.
const KNOB_LABELS = {
  node_jitter: "Node jitter",
  relax_min_sep_frac: "Min sep",
  lloyd_passes: "Lloyd passes",
  extra_edge_fraction: "Extra edges",
  max_edge_length_frac: "Max edge",
  ship_ly_per_turn: "Ship speed",
  ship_speed_growth_pct: "Speed growth",
  home_start_ships: "Start ships",
  home_production: "Home prod",
  garrison_base: "Garrison base",
  garrison_k: "Garrison scale",
  garrison_jitter: "Garrison jitter",
  combat_jitter: "Combat jitter",
  defender_advantage: "Def adv",
  neutral_produces: "Neutral prod",
  fog_sight: "Fog sight",
  fog_scout: "Fog scout",
};

// Fields settings_json may carry that aren't a "tweak": the match's identity
// (always present), the score (only on a challenge link), the per-seat
// strategy/param lists (read separately — see botsOf below and the synthetic
// "Tuned AI" entry), and autoplay (a play-style preference, excluded from
// sc_config_key itself, per schema.sql).
const NOT_A_TWEAK = new Set([
  "mode", "players", "nodes", "seed", "autoplay", "challenge", "ai", "ai_strategy",
]);

function formatValue(key, value) {
  const label = KNOB_LABELS[key] || key;
  return typeof value === "boolean" ? `${label} ${value ? "on" : "off"}` : `${label} ${value}`;
}

/**
 * The non-default knobs on a stored setup, as {key, label} pairs — free, since
 * settings_json is already pruned to the diff. An unrecognised key (a
 * hand-edited link, or a field added after this module was written) still
 * renders, under its own name rather than being silently dropped. Sorted by
 * key so two configs sharing the same tweaks read identically.
 */
export function tweaks(settingsJson) {
  const settings = settingsJson || {};
  const out = Object.keys(settings)
    .filter((key) => !NOT_A_TWEAK.has(key))
    .map((key) => ({ key, label: formatValue(key, settings[key]) }));
  if (Array.isArray(settings.ai) && settings.ai.length) out.push({ key: "ai", label: "Tuned AI" });
  return out.sort((a, b) => a.key.localeCompare(b.key));
}

/**
 * "Default", one or two tweaks spelled out, or "N tweaks" beyond that — the
 * auto label for a config nobody has named. A submitted name always overrides
 * this; see configTitle().
 */
export function configLabel(settingsJson) {
  const t = tweaks(settingsJson);
  if (!t.length) return "Default";
  if (t.length <= 2) return t.map((x) => x.label).join(" · ");
  return `${t.length} tweaks`;
}

/** The title a config badge shows: a submitted name first, else the derived label. */
export function configTitle(game) {
  return (game.config_name || "").trim() || configLabel(game.settings_json);
}
