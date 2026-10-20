// Tag normalisation, pure — no DOM, no fetch — so the score-submission
// screen's tag field and the tests can both exercise the exact rule the
// database itself enforces (`config_tags_tag_shape` in schema.sql), without a
// browser or a database to check it against.

// Mirrors `configs_tags_shape`'s per-tag limits in schema.sql: lowercase
// letters, digits, spaces, `+`/`-`, 1-24 characters. One tag per element, so
// no comma in the charset — that's the separator on the way in.
const MAX_TAGS = 6;
const MAX_TAG_LEN = 24;
const TAG_SHAPE = /^[a-z0-9 +-]+$/;

/**
 * `"Fun, Fun , grindy!"` -> `["fun", "grindy"]` — trim, lowercase, drop
 * anything empty or outside the allowed shape (silently, the same way a
 * comma-separated field elsewhere on this site tolerates stray punctuation),
 * dedupe, and cap at 6. The cap and the shape are pre-checks only: the
 * database's own `config_tags_tag_shape`/`config_tags_unique` constraints are
 * what's actually authoritative, since a tag can also arrive with none of
 * this normalisation applied (a hand-built request straight to PostgREST).
 */
export function normalizeTags(rawString) {
  return [...new Set(
    (rawString || "").split(",")
      .map((tag) => tag.trim().toLowerCase())
      .filter((tag) => tag.length > 0 && tag.length <= MAX_TAG_LEN && TAG_SHAPE.test(tag)),
  )].slice(0, MAX_TAGS);
}
