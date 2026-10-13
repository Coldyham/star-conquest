// Signing in with Google, for one thing only: owning a claimed name.
//
// Supabase Auth runs the Google round trip; this file is the PKCE flow over
// plain fetch, for the same reason api.mjs loads no client library. Nobody has
// to sign in to play, post or watch — an unclaimed name works exactly as it
// always did — so every failure here degrades to "signed out", never an error a
// page can't render past.
//
// The session lives in localStorage under SESSION_KEY. The board shares the
// game's origin, so the web game reads the same value (`paths.WEB_AUTH_KEY`,
// pinned by tests/test_leaderboard_sync.py) to name a claimed play-by-post seat.

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "./config.mjs";

export const SESSION_KEY = "sc_auth";
const VERIFIER_KEY = "sc_auth_verifier";
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

function base64url(bytes) {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A fresh PKCE verifier: 32 random bytes, base64url. */
export function newVerifier() {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

/** RFC 7636's S256 challenge for `verifier`. */
export async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** Where Google sends the browser back: the account page on this deploy. */
export function returnUrl(loc = globalThis.location) {
  return new URL("account.html", loc.href).href.split(/[?#]/)[0];
}

/** The authorize URL to leave for. Separate from signIn() so a test can read it. */
export async function authorizeUrl(verifier, redirectTo) {
  const params = new URLSearchParams({
    provider: "google",
    redirect_to: redirectTo,
    code_challenge: await challengeFor(verifier),
    code_challenge_method: "s256",
  });
  return `${SUPABASE_URL}/auth/v1/authorize?${params}`;
}

export async function signIn() {
  const verifier = newVerifier();
  write("sessionStorage", VERIFIER_KEY, verifier);
  globalThis.location.assign(await authorizeUrl(verifier, returnUrl()));
}

export function signOut() {
  write("localStorage", SESSION_KEY, null);
}

/** Supabase's token answer, cut down to what this site keeps. */
function kept(body, now) {
  return {
    access_token: body.access_token,
    refresh_token: body.refresh_token,
    expires_at: body.expires_at || Math.floor(now / 1000) + (body.expires_in || 3600),
    email: body.user?.email || "",
    user_id: body.user?.id || "",
  };
}

async function tokenCall(grant, payload, now) {
  const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=${grant}`, {
    method: "POST",
    headers: { apikey: SUPABASE_ANON_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return null;
  const body = await res.json();
  if (!body.access_token) return null;
  const session = kept(body, now);
  write("localStorage", SESSION_KEY, session);
  return session;
}

/**
 * Finish the round trip on the account page: trade `?code=` for a session.
 * Returns the session, or null when there is no code here or the trade failed.
 * Either way the code leaves the address bar, so a reload doesn't retry it.
 */
export async function finishSignIn(loc = globalThis.location, now = Date.now()) {
  const url = new URL(loc.href);
  const code = url.searchParams.get("code");
  if (!code) return null;
  url.searchParams.delete("code");
  try {
    globalThis.history?.replaceState(null, "", url.pathname + url.search + url.hash);
  } catch {
    // A page with no history API keeps the code in view; harmless, it is spent.
  }
  let verifier = null;
  try {
    verifier = store("sessionStorage")?.getItem(VERIFIER_KEY) || null;
  } catch {
    // Blocked storage: no verifier, so no session — the signed-out case.
  }
  write("sessionStorage", VERIFIER_KEY, null);
  if (!verifier) return null;
  try {
    return await tokenCall("pkce", { auth_code: code, code_verifier: verifier }, now);
  } catch {
    return null;
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
    const fresh = await tokenCall("refresh_token", { refresh_token: current.refresh_token }, now);
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
