// Reads a Star Conquest challenge link.
//
// The game encodes a whole Settings object as
// `JSON -> zlib.compress(9) -> base64url, '=' stripped` (starconquest/settings.py,
// Settings.to_token). This is the reading half, in JavaScript, so the leaderboard
// can take a pasted link without any Python.
//
// It reads only what the leaderboard needs — the four identity fields the game
// never prunes, plus the score — rather than porting Settings.from_dict. The
// decompressor is injected so this module stays platform-free: the browser passes
// inflate-browser.mjs, the tests pass Node's zlib.
//
// tests/fixtures/tokens.json holds real tokens from the Python encoder;
// regenerate with `uv run python tools/dump_challenge_fixtures.py`.

const MODES = ["random", "symmetric"];

// Checksums that a since-superseded version of the game stamped, mapped to what
// the same setup hashes to now (settings._LEGACY_KEY_DROPS is the game's own half
// of this). Adding a field to Settings moves every digest, so a link shared before
// that change groups apart from links to the identical map unless it is folded
// here. Only reachable keys can be listed — the Python digest cannot be recomputed
// in JS — so an entry is added when such a link actually turns up. Rows already
// stored under the old key are moved by leaderboard/fold-game-key.sql.
const KEY_ALIASES = {
  // 3 players, 18 nodes, seed 879758, two thinkers — stamped before the
  // defender-advantage knob. Its target was superseded in turn when in-lane
  // battles joined Settings (that setup now stamps a87ef2158a2ae0cf), which is
  // this table's standing flaw: a target is only current until the next field
  // lands. Repointing it would move nothing, since findTwin() in submit.mjs
  // reaches the same row by the setup itself, so it stays as the record of what
  // was actually stored.
  "3e7b44384effd7b2": "665714b9291851c6",
  // 3 players, 13 nodes, seed 882369, marshal + knower — stamped before in-lane
  // battles joined Settings (merged to main 2026-09-04, days after the board
  // went live), so replaying it filed the new score under a second key. The
  // setup's defender advantage is off its default, which is why the game offers
  // only this one legacy digest for it: challenge_keys() is
  // ('7f7fabfca0969ba4', 'e2954098c1a26e02').
  "e2954098c1a26e02": "7f7fabfca0969ba4",
};

/**
 * A stamped key resolved through KEY_ALIASES, following a chain to its end: a
 * digest superseded twice (a map that outlived two schema changes) has an entry
 * pointing at an entry. The visited set is not defensive tidiness — a typo that
 * pointed two entries at each other would otherwise hang the page it is called
 * from, and this table is edited by hand.
 *
 * A key with no entry is returned unchanged, which is every key on a board that
 * has never split.
 */
export function aliasFor(key) {
  let current = key;
  const seen = new Set([current]);
  while (KEY_ALIASES[current] && !seen.has(KEY_ALIASES[current])) {
    current = KEY_ALIASES[current];
    seen.add(current);
  }
  return current;
}

/** Everything after the '#', since people paste a whole URL, not a bare token. */
export function fragmentOf(input) {
  const text = String(input).trim();
  const hash = text.indexOf("#");
  return (hash >= 0 ? text.slice(hash + 1) : text).trim();
}

/**
 * Decode a challenge link (or bare token).
 *
 * @param input    the pasted URL or token
 * @param inflate  (Uint8Array) => Uint8Array | Promise<Uint8Array>, zlib-wrapped
 * @returns {mode, players, nodes, seed, challenge: {turns, lost, hand, by}, gameKey, token}
 * @throws Error  'malformed token' or 'not a challenge link'
 */
export async function decodeToken(input, inflate) {
  const token = fragmentOf(input);
  if (!token) throw new Error("malformed token: empty");

  let dict;
  try {
    const bytes = base64urlToBytes(token);
    // Plain JSON always starts '{', which zlib output never does — the game's own
    // test for links shared before compression landed.
    const raw = bytes[0] === 0x7b ? bytes : await inflate(bytes);
    dict = JSON.parse(new TextDecoder().decode(raw));
  } catch (err) {
    throw new Error(`malformed token: ${err.message}`);
  }
  if (!dict || typeof dict !== "object" || Array.isArray(dict)) {
    throw new Error("malformed token: not an object");
  }

  // turns <= 0 is Challenge's own "no challenge" sentinel, so a plain
  // settings-share link lands here too — there is no score in it to post.
  const challenge = dict.challenge;
  if (!challenge || typeof challenge !== "object" || !(positiveInt(challenge.turns))) {
    throw new Error("not a challenge link");
  }

  return {
    // from_dict clamps an unknown mode to "random"; match it rather than reject.
    mode: MODES.includes(dict.mode) ? dict.mode : "random",
    players: requirePositiveInt(dict.players, "players"),
    nodes: requirePositiveInt(dict.nodes, "nodes"),
    // A challenge link always pins the seed actually played (main.challenge_settings),
    // so a missing one would silently describe a different map.
    seed: requireInt(dict.seed, "seed"),
    challenge: {
      turns: challenge.turns,
      lost: requireCount(challenge.lost, "lost"),
      hand: requireCount(challenge.hand, "hand"),
      by: typeof challenge.by === "string" ? challenge.by : "",
    },
    gameKey: await gameKeyFor(dict),
    setup: setupOf(dict),
    token,
  };
}

/**
 * The leaderboard's id for "this exact setup".
 *
 * Normally the game's own checksum, riding along in the token: Challenge.key is
 * Settings.challenge_key(), which main.py always stamps. Reusing it means the
 * site groups scores by exactly what the game calls the same match, and needs no
 * hashing here at all.
 *
 * Challenge.matches takes a blank key on trust (hand-written links), so those get
 * a hash of the setup instead, prefixed to keep the two provenances distinct.
 * That hash is only ever compared against others computed here — Python and JS
 * disagree on integral floats (1.0 vs 1), so it is not a cross-language value.
 *
 * A stamped key listed in KEY_ALIASES resolves to its current form first.
 */
export async function gameKeyFor(dict) {
  const key = dict.challenge && dict.challenge.key;
  if (typeof key === "string" && key.trim()) {
    return aliasFor(key.trim());
  }

  const { challenge, autoplay, ...setup } = dict;
  const canon = JSON.stringify(canonicalize(setup));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canon));
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `j:${hex.slice(0, 16)}`;
}

/** The settings a game row stores: everything but the score. */
export function setupOf(dict) {
  const { challenge, ...setup } = dict;
  return setup;
}

/**
 * A comparable form of a stored `games.settings_json`: "the same map", derived
 * from the setup itself rather than from a checksum some version of the game
 * stamped.
 *
 * This is the fold that KEY_ALIASES above cannot be — a hand-kept alias needs a
 * split to be noticed and reported first, and its target is only current until
 * the next field joins Settings. Two rows written by different versions of the
 * game carry the *same* settings_json: `token_dict` prunes every field still at
 * its default, so a field added since is simply absent from both. Comparing that
 * is exactly the identity `sc_config_key` already groups configs by
 * (schema.sql), seed included here since a seed is part of a map.
 *
 * `autoplay` is dropped for the same reason it is excluded from the game's own
 * `challenge_key`: watching the AI play is not a different map. Keys are sorted
 * so two writers that emitted them in a different order still agree.
 */
export function setupIdentity(setup) {
  const { autoplay, ...rest } = setup || {};
  return JSON.stringify(canonicalize(rest));
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize); // positional, keep order
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, canonicalize(value[k])]));
  }
  return value;
}

function base64urlToBytes(token) {
  const b64 = token.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const binary = atob(padded); // throws on any character outside the alphabet
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

function positiveInt(value) {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function requireInt(value, name) {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`malformed token: ${name} is not a whole number`);
  }
  return value;
}

function requirePositiveInt(value, name) {
  if (!positiveInt(value)) throw new Error(`malformed token: ${name} is not a positive number`);
  return value;
}

function requireCount(value, name) {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error(`malformed token: ${name} is not a count`);
  }
  return value;
}
