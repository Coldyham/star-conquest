// Signing in with a private login and a password, for one thing only: owning a
// claimed name.
//
// Supabase Auth keeps the accounts; this file talks to it over plain fetch, for
// the same reason api.mjs loads no client library. Nobody has to sign in to
// play, post or watch — an unclaimed name works exactly as it always did — so
// every failure here degrades to "signed out", never an error a page can't
// render past.
//
// Supabase wants an email for a password account, so the login is turned into
// one that names nothing: a hash of it at ACCOUNT_DOMAIN (`accountEmail`). The
// login can be any text, an email address included, and Supabase never holds
// it. Nothing is ever mailed: email confirmation is off in the project.
//
// The session lives in localStorage under SESSION_KEY. The board shares the
// game's origin, so the web game reads the same value (`paths.WEB_AUTH_KEY`,
// pinned by tests/test_leaderboard_sync.py) to name a claimed play-by-post seat.

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "./config.mjs";

export const SESSION_KEY = "sc_auth";
// Never change it: every account's stored email ends in it, so a new one would
// lock every existing account out. `tools/admin.py` mirrors it
// (tests/test_leaderboard_sync.py).
export const ACCOUNT_DOMAIN = "star-conquest.netlify.app";
export const MIN_PASSWORD = 8;
// Refresh this long before the access token would lapse, so a request never
// leaves with one that expires in flight.
const REFRESH_EARLY_S = 60;

function store(kind) {
  try {
    return globalThis[kind] || null;
  } catch {
    return null;
  }
}

function readJson(kind, key) {
  try {
    const raw = store(kind)?.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function write(kind, key, value) {
  try {
    if (value === null) store(kind)?.removeItem(key);
    else store(kind)?.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch {
    // Blocked storage: sign-in just doesn't stick, which is the signed-out case.
  }
}

/** A login as the account knows it: case and surrounding space folded. */
export const loginKey = (login) => String(login || "").trim().toLowerCase();

/**
 * The email Supabase holds for `login`: the first 40 hex digits of its key's
 * SHA-256, at ACCOUNT_DOMAIN. `tools/admin.py`'s `account_email` is the same.
 */
export async function accountEmail(login) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(loginKey(login)));
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 40)}@${ACCOUNT_DOMAIN}`;
}

export function signOut() {
  write("localStorage", SESSION_KEY, null);
}

/** Supabase's token answer, cut down to what this site keeps. */
function kept(body, now, login) {
  return {
    access_token: body.access_token,
    refresh_token: body.refresh_token,
    expires_at: body.expires_at || Math.floor(now / 1000) + (body.expires_in || 3600),
    login: login || "",
    user_id: body.user?.id || "",
  };
}

async function tokenCall(grant, payload, now, login) {
  const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=${grant}`, {
    method: "POST",
    headers: { apikey: SUPABASE_ANON_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return null;
  const body = await res.json();
  if (!body.access_token) return null;
  const session = kept(body, now, login);
  write("localStorage", SESSION_KEY, session);
  return session;
}

/**
 * Auth's refusal as a sentence. Supabase answers `{error_code, msg}`; an older
 * server `{error, error_description}`.
 */
export function authError(body) {
  const code = body?.error_code || body?.code || body?.error || "";
  switch (code) {
    case "invalid_credentials":
    case "invalid_grant":
      return "That login and password don't match.";
    case "user_already_exists":
    case "email_exists":
      return "That login is taken. Sign in instead, or pick another.";
    case "weak_password":
      return `Choose a longer or less common password (at least ${MIN_PASSWORD} characters).`;
    case "over_request_rate_limit":
      return "Too many tries. Wait a few minutes and try again.";
    case "signup_disabled":
      return "New accounts are turned off at the moment.";
    default:
      return body?.msg || body?.error_description || body?.message || "Couldn't sign in.";
  }
}

async function credentials(path, login, password, now) {
  if (!loginKey(login)) return { error: "Type a login." };
  if (String(password || "").length < MIN_PASSWORD) {
    return { error: `A password is at least ${MIN_PASSWORD} characters.` };
  }
  let res;
  let body;
  try {
    res = await fetch(`${SUPABASE_URL}/auth/v1/${path}`, {
      method: "POST",
      headers: { apikey: SUPABASE_ANON_KEY, "Content-Type": "application/json" },
      body: JSON.stringify({ email: await accountEmail(login), password }),
    });
    body = await res.json().catch(() => ({}));
  } catch {
    return { error: "Couldn't reach the sign-in server." };
  }
  if (!res.ok) return { error: authError(body) };
  // A sign-up answered with no session means the project still asks for email
  // confirmation, which can never arrive here.
  if (!body.access_token) return { error: "Sign-up needs email confirmation turned off in Supabase." };
  const session = kept(body, now, login.trim());
  write("localStorage", SESSION_KEY, session);
  return { session };
}

/** Make an account and sign in to it: `{session}` or `{error}`, never a throw. */
export function signUp(login, password, now = Date.now()) {
  return credentials("signup", login, password, now);
}

/** Sign in to an existing account: `{session}` or `{error}`, never a throw. */
export function signIn(login, password, now = Date.now()) {
  return credentials("token?grant_type=password", login, password, now);
}

/** Set a new password for the signed-in account: "" when done, else why not. */
export async function changePassword(password) {
  if (String(password || "").length < MIN_PASSWORD) return `A password is at least ${MIN_PASSWORD} characters.`;
  const live = await session();
  if (!live) return "Sign in first.";
  try {
    const res = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
      method: "PUT",
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${live.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ password }),
    });
    if (res.ok) return "";
    return authError(await res.json().catch(() => ({})));
  } catch {
    return "Couldn't reach the sign-in server.";
  }
}

/** The stored session as-is, without refreshing — for code that can't await. */
export function storedSession() {
  const session = readJson("localStorage", SESSION_KEY);
  return session && session.access_token ? session : null;
}

/**
 * The live session, refreshed first if it is about to lapse; null when signed
 * out or when a refresh fails (the stored one is then dropped, since a refresh
 * token that was refused once will be refused again).
 */
export async function session(now = Date.now()) {
  const current = storedSession();
  if (!current) return null;
  if (current.expires_at - REFRESH_EARLY_S > now / 1000) return current;
  try {
    const fresh = await tokenCall("refresh_token", { refresh_token: current.refresh_token }, now,
      current.login);
    if (fresh) return fresh;
  } catch {
    // Unreachable auth server: treat as signed out for this request, but keep
    // the stored session so the next page load can try again.
    return null;
  }
  signOut();
  return null;
}

/** The bearer header for a signed-in caller, or {} for an anonymous one. */
export async function authHeader(now = Date.now()) {
  const live = await session(now);
  return live ? { Authorization: `Bearer ${live.access_token}` } : {};
}
