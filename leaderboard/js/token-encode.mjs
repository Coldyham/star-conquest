// Writes a Star Conquest settings link — the other half of token-decode.mjs.
//
// The site reads links for the scores on them; this is the one place it writes
// one, so a config's page can offer that setup on a fresh map. The encoding is
// the game's own (`JSON -> zlib.compress(9) -> base64url, '=' stripped`,
// starconquest/settings.py, Settings.to_token), so the result is a link the game
// cannot tell from one it shared itself.
//
// No DOM and no compressor in here: the deflate is injected exactly as
// token-decode.mjs injects its inflate, so tests can drive it with node:zlib.

/**
 * A stored setup (games.settings_json) with the map set aside, ready to encode:
 * what this site means by "a config" rather than "a map".
 *
 * Drops the same two keys `public.sc_config_key` does (schema.sql), and for the
 * same reason — the seed and autoplay are exactly what the games in a config
 * group are allowed to differ in, so neither belongs in a link offering the
 * setup instead of one of its maps.
 *
 * `seed: null` rather than a dropped key because null is what the game itself
 * writes for "roll a fresh map at start" (`Settings.seed` is `Optional[int]`,
 * and `_TOKEN_ALWAYS` emits it either way); `from_dict` reads both the same.
 *
 * There is no score to strip here: settings_json is stored through `setupOf`,
 * which already leaves the challenge behind.
 */
export function newSeedSetup(settingsJson) {
  const { autoplay, ...setup } = settingsJson || {};
  return { ...setup, seed: null };
}

/**
 * A setup dict as a link fragment.
 *
 * @param setup    a plain settings dict (see newSeedSetup)
 * @param deflate  (Uint8Array) => Uint8Array | Promise<Uint8Array>, zlib-wrapped;
 *                 or null to emit the JSON uncompressed, which every reader still
 *                 takes — both halves sniff for the leading '{' plain JSON has and
 *                 zlib output never does.
 */
export async function encodeToken(setup, deflate) {
  const json = new TextEncoder().encode(JSON.stringify(setup));
  return bytesToBase64url(deflate ? await deflate(json) : json);
}

function bytesToBase64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
