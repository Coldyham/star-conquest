// Claimed names, as the pages explain them. The rule itself is schema.sql's
// (`sc_may_use_user`, `claim_name`) and pbp.mjs's; this only turns it into words
// before a write, so a player sees why rather than a bare refusal.

/** Shown wherever a typed name turns out to belong to someone else. */
export const CLAIMED_MESSAGE =
  "That name is claimed. Sign in as its owner (Account page), or pick another name.";

const key = (name) => String(name || "").trim().toLowerCase();

/**
 * Whether `row` (a users row with `claimed`, or undefined for a name with no row
 * yet) may not be posted under by someone whose own claimed name is `ownName`
 * ("" when signed out or owning none).
 */
export function nameBlocked(row, ownName) {
  if (!row || !row.claimed) return false;
  return key(row.name) !== key(ownName);
}

/** claim_name's fixed error messages (schema.sql) as a sentence for the page. */
export function claimError(message) {
  const text = String(message || "");
  if (/sign in/i.test(text)) return "Sign in first.";
  if (/already own/i.test(text)) return "You already own a name — release it first to claim another.";
  if (/name in use/i.test(text)) {
    return "That name has already been used on the board, so it can't be claimed here. " +
      "If it's yours, ask the board's owner to assign it to you.";
  }
  if (/bad name/i.test(text)) return "A name is 1 to 60 characters.";
  return text || "Couldn't claim that name.";
}
