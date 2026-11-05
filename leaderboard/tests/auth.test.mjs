// auth.mjs: login and password, and the stored session, over a stub fetch and
// stub storage. Signing in is optional, so the property that matters as much as
// "it works" is "a failure reads as signed out": blocked storage, a refused
// password or a dead refresh token must never throw at a page.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  ACCOUNT_DOMAIN, accountEmail, authHeader, changePassword, SESSION_KEY, session, signIn, signUp,
  storedSession,
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

test("a login becomes a hashed email that names nothing", async () => {
  const email = await accountEmail("  Rival42 ");
  const hex = createHash("sha256").update("rival42").digest("hex").slice(0, 40);
  assert.equal(email, `${hex}@${ACCOUNT_DOMAIN}`);
  assert.equal(await accountEmail("RIVAL42"), email, "case and spaces fold");
  assert.doesNotMatch(await accountEmail("ann@example.com"), /ann|example/, "an email login is hidden too");
});

test("creating an account sends the hashed email and keeps the login", withStorage(async () => {
  const calls = answer({ access_token: "a1", refresh_token: "r1", expires_in: 3600, user: { id: "uid-1" } });
  const got = await signUp(" Rival42 ", "long enough", 1_000_000);
  assert.equal(got.session.access_token, "a1");
  assert.equal(got.session.expires_at, 1000 + 3600);
  assert.match(calls[0].url, /\/auth\/v1\/signup$/);
  assert.deepEqual(JSON.parse(calls[0].init.body),
    { email: await accountEmail("rival42"), password: "long enough" });
  assert.equal(storedSession().login, "Rival42");
  assert.equal(storedSession().email, undefined, "no email is kept");
}));

test("signing in asks for a password grant", withStorage(async () => {
  const calls = answer({ access_token: "a1", refresh_token: "r1", expires_in: 3600 });
  assert.equal((await signIn("ann@example.com", "long enough")).session.login, "ann@example.com");
  assert.match(calls[0].url, /\/auth\/v1\/token\?grant_type=password$/);
}));

test("a refusal is a sentence and no session", withStorage(async () => {
  answer({ error_code: "invalid_credentials", msg: "Invalid login credentials" }, 400);
  assert.deepEqual(await signIn("ann", "long enough"), { error: "That login and password don't match." });
  answer({ error_code: "user_already_exists" }, 422);
  assert.match((await signUp("ann", "long enough")).error, /taken/);
  assert.equal(storedSession(), null);
}));

test("a short password or no login never reaches Auth", withStorage(async () => {
  const calls = answer({ access_token: "a1" });
  assert.match((await signUp("ann", "short")).error, /at least 8/);
  assert.match((await signIn("  ", "long enough")).error, /login/);
  assert.equal(calls.length, 0);
}));

test("a sign-up with no session means confirmation is still on", withStorage(async () => {
  answer({ id: "uid-1", email: "x" });
  assert.match((await signUp("ann", "long enough")).error, /confirmation/);
  assert.equal(storedSession(), null);
}));

test("a new password goes to the signed-in account", withStorage(async () => {
  localStorage.setItem(SESSION_KEY, JSON.stringify(
    { access_token: "a", refresh_token: "r", expires_at: 9e9, login: "ann" }));
  const calls = answer({ id: "uid-1" });
  assert.equal(await changePassword("long enough"), "");
  assert.equal(calls[0].init.method, "PUT");
  assert.equal(calls[0].init.headers.Authorization, "Bearer a");
  assert.deepEqual(JSON.parse(calls[0].init.body), { password: "long enough" });
  localStorage.removeItem(SESSION_KEY);
  assert.equal(await changePassword("long enough"), "Sign in first.");
}));

test("a session near expiry is refreshed; a refused refresh signs out", withStorage(async () => {
  const now = 2_000_000;
  localStorage.setItem(SESSION_KEY, JSON.stringify(
    { access_token: "old", refresh_token: "r1", expires_at: now / 1000 + 10, login: "ann" }));
  const calls = answer({ access_token: "new", refresh_token: "r2", expires_in: 3600 });
  assert.equal((await session(now)).access_token, "new");
  assert.equal(storedSession().login, "ann", "a refresh keeps the login");
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
