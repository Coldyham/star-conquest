// auth.mjs: the PKCE round trip and the stored session, over a stub fetch and
// stub storage. Signing in is optional, so the property that matters as much as
// "it works" is "a failure reads as signed out": blocked storage, a refused
// code or a dead refresh token must never throw at a page.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  authHeader, authorizeUrl, challengeFor, finishSignIn, newVerifier, SESSION_KEY,
  session, storedSession,
} from "../js/auth.mjs";
import { claimError, nameBlocked } from "../js/claims.mjs";

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
    data,
  };
}

function withStorage(fn) {
  return async () => {
    globalThis.localStorage = memoryStorage();
    globalThis.sessionStorage = memoryStorage();
    const realFetch = globalThis.fetch;
    try {
      await fn();
    } finally {
      globalThis.fetch = realFetch;
      delete globalThis.localStorage;
      delete globalThis.sessionStorage;
    }
  };
}

function answer(body, status = 200) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status });
  };
  return calls;
}

test("the challenge is base64url SHA-256 of the verifier", async () => {
  const verifier = newVerifier();
  assert.match(verifier, /^[A-Za-z0-9_-]{43}$/);
  const expected = createHash("sha256").update(verifier).digest("base64url");
  assert.equal(await challengeFor(verifier), expected);
});

test("the authorize URL asks Google, with the challenge and the way back", async () => {
  const url = new URL(await authorizeUrl("v".repeat(43), "https://x.netlify.app/board/account.html"));
  assert.equal(url.pathname, "/auth/v1/authorize");
  assert.equal(url.searchParams.get("provider"), "google");
  assert.equal(url.searchParams.get("code_challenge_method"), "s256");
  assert.equal(url.searchParams.get("code_challenge"), await challengeFor("v".repeat(43)));
  assert.equal(url.searchParams.get("redirect_to"), "https://x.netlify.app/board/account.html");
});

test("a returned code is traded for a session that is then stored", withStorage(async () => {
  sessionStorage.setItem("sc_auth_verifier", "the-verifier");
  const calls = answer({ access_token: "a1", refresh_token: "r1", expires_in: 3600,
                         user: { id: "uid-1", email: "p@example.com" } });
  const got = await finishSignIn({ href: "https://x/board/account.html?code=c0de" }, 1_000_000);
  assert.equal(got.access_token, "a1");
  assert.equal(got.expires_at, 1000 + 3600);
  assert.match(calls[0].url, /\/auth\/v1\/token\?grant_type=pkce$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { auth_code: "c0de", code_verifier: "the-verifier" });
  assert.equal(storedSession().email, "p@example.com");
  assert.equal(sessionStorage.getItem("sc_auth_verifier"), null, "a verifier is single use");
}));

test("no code, no verifier or a refused code is simply no session", withStorage(async () => {
  assert.equal(await finishSignIn({ href: "https://x/board/account.html" }), null);
  answer({ access_token: "a1" });
  assert.equal(await finishSignIn({ href: "https://x/a.html?code=c" }), null, "no verifier stored");
  sessionStorage.setItem("sc_auth_verifier", "v");
  answer({ error: "invalid_grant" }, 400);
  assert.equal(await finishSignIn({ href: "https://x/a.html?code=c" }), null);
  assert.equal(storedSession(), null);
}));

test("a session near expiry is refreshed; a refused refresh signs out", withStorage(async () => {
  const now = 2_000_000;
  localStorage.setItem(SESSION_KEY, JSON.stringify(
    { access_token: "old", refresh_token: "r1", expires_at: now / 1000 + 10 }));
  const calls = answer({ access_token: "new", refresh_token: "r2", expires_in: 3600 });
  assert.equal((await session(now)).access_token, "new");
  assert.match(calls[0].url, /grant_type=refresh_token$/);
  assert.deepEqual(await authHeader(now), { Authorization: "Bearer new" });

  localStorage.setItem(SESSION_KEY, JSON.stringify(
    { access_token: "old", refresh_token: "dead", expires_at: 0 }));
  answer({ error: "invalid_grant" }, 400);
  assert.equal(await session(now), null);
  assert.equal(localStorage.getItem(SESSION_KEY), null);
  assert.deepEqual(await authHeader(now), {});
}));

test("a fresh session is used as-is, with no call to Auth", withStorage(async () => {
  localStorage.setItem(SESSION_KEY, JSON.stringify(
    { access_token: "a", refresh_token: "r", expires_at: 9e9 }));
  const calls = answer({});
  assert.equal((await session()).access_token, "a");
  assert.equal(calls.length, 0);
}));

test("storage that throws reads as signed out, never as an error", async () => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true, get() { throw new Error("blocked"); } });
  try {
    assert.equal(storedSession(), null);
    assert.deepEqual(await authHeader(), {});
  } finally {
    delete globalThis.localStorage;
  }
});

test("a claimed name blocks everyone but the owner; an unclaimed one nobody", () => {
  assert.equal(nameBlocked(undefined, ""), false, "a name with no row yet");
  assert.equal(nameBlocked({ name: "Ann", claimed: false }, ""), false);
  assert.equal(nameBlocked({ name: "Ann", claimed: true }, ""), true, "signed out");
  assert.equal(nameBlocked({ name: "Ann", claimed: true }, "Bob"), true);
  assert.equal(nameBlocked({ name: "Ann", claimed: true }, " ann "), false, "the owner, case-folded");
});

test("claim_name's errors become sentences", () => {
  assert.match(claimError("name in use"), /already been used/);
  assert.match(claimError("you already own a name"), /release it first/);
  assert.match(claimError("sign in to claim a name"), /Sign in/);
  assert.equal(claimError("something else"), "something else");
});
