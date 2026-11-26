// The phone app's sign-in (mobile/app/auth.js), in Node: an expired access
// token is renewed from the refresh token with no sign-in, and only
// Cognito's own refusal ends the login. Cognito and the API are fakes; no
// real token is used. Run by tests/test_mobile_auth.py and make mobile-test.
import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../mobile/app/auth.js", import.meta.url), "utf8");
const ctx = {};
ctx.globalThis = ctx;
vm.runInNewContext(source, ctx);
const { create, KEY } = ctx.PodbayAuth;

const COGNITO = "https://cognito-idp.eu-north-1.amazonaws.com/";
const T0 = 1_800_000_000_000;

function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}

// A world with a phone that signed in long ago: its access token ran out
// an hour before T0, its refresh token is still good.
function world({ cognito, apiAccepts = (token) => token === "access-2" } = {}) {
  const storage = memoryStorage();
  storage.setItem(KEY, JSON.stringify({ access: "access-1", refresh: "refresh-1", exp: T0 - 3600_000, email: "phone@example.test", clientId: "client" }));
  const calls = { cognito: [], api: [] };
  const fetch = (url, init) => {
    if (url === COGNITO) {
      const body = JSON.parse(init.body);
      calls.cognito.push({ target: init.headers["X-Amz-Target"], body });
      return cognito(body, calls.cognito.length);
    }
    const token = (init.headers.Authorization || "").replace("Bearer ", "");
    calls.api.push(token);
    return Promise.resolve(apiAccepts(token) ? response(200, { ok: true }) : response(401, { message: "Unauthorized" }));
  };
  const auth = create({ storage, fetch, region: "eu-north-1", clientId: "client", now: () => T0 });
  return { auth, storage, calls, stored: () => JSON.parse(storage.getItem(KEY) || "null") };
}

const renewed = () => Promise.resolve(response(200, { AuthenticationResult: { AccessToken: "access-2", ExpiresIn: 900, IdToken: "id-2" } }));
const refused = (message) => () => Promise.resolve(response(400, { __type: "NotAuthorizedException", message }));
const unreachable = () => Promise.reject(new TypeError("Load failed"));

// 1. Expired access token: renewed with REFRESH_TOKEN_AUTH, the API call goes
//    through with the new token, and the login stays. Three calls at once
//    (state, me, messages at start) renew once.
{
  const w = world({ cognito: renewed });
  const results = await Promise.all(["state", "me", "messages"].map((p) => w.auth.authorized("/api/" + p, { method: "GET" })));
  assert.deepEqual(results.map((r) => r.status), [200, 200, 200]);
  assert.equal(w.calls.cognito.length, 1, "one renewal for three calls");
  assert.equal(w.calls.cognito[0].target, "AWSCognitoIdentityProviderService.InitiateAuth");
  assert.equal(w.calls.cognito[0].body.AuthFlow, "REFRESH_TOKEN_AUTH");
  assert.equal(w.calls.cognito[0].body.AuthParameters.REFRESH_TOKEN, "refresh-1");
  assert.deepEqual(w.calls.api, ["access-2", "access-2", "access-2"]);
  assert.equal(w.stored().access, "access-2");
  assert.equal(w.stored().refresh, "refresh-1", "Cognito sends no new refresh token; the old one stays");
  assert.equal(w.stored().exp, T0 + 900_000);
  assert.ok(w.auth.signedIn(), "still signed in");
}

// 2. No network when the app wakes (iOS "Load failed"): the call fails, the
//    login stays, and the next call renews once the network is back.
{
  let online = false;
  const w = world({ cognito: () => (online ? renewed() : unreachable()) });
  await assert.rejects(w.auth.authorized("/api/state", { method: "GET" }), (e) => e.offline === true && !e.signedOut);
  assert.ok(w.auth.signedIn(), "a failed renewal must not sign the phone out");
  assert.equal(w.stored().refresh, "refresh-1");
  online = true;
  const r = await w.auth.authorized("/api/state", { method: "GET" });
  assert.equal(r.status, 200);
  assert.equal(w.stored().access, "access-2");
}

// 3. Cognito answers but with an error that is not about the token
//    (throttling, a 500): the login stays.
for (const reply of [
  () => Promise.resolve(response(400, { __type: "TooManyRequestsException", message: "Rate exceeded" })),
  () => Promise.resolve(response(500, { __type: "InternalErrorException", message: "boom" })),
]) {
  const w = world({ cognito: reply });
  await assert.rejects(w.auth.accessToken(), (e) => e.offline === true);
  assert.ok(w.auth.signedIn(), "a Cognito error that is not a refusal keeps the login");
}

// 4. Cognito refuses the refresh token (30 days over, or signed out with
//    podbay mobile signout): the login ends and the app shows the sign-in.
{
  const w = world({ cognito: refused("Refresh Token has expired") });
  await assert.rejects(w.auth.authorized("/api/state", { method: "GET" }), (e) => e.signedOut === true);
  assert.equal(w.stored(), null);
  assert.ok(!w.auth.signedIn());
}

// 5. The API answers 401 to a token the phone thought was good: renew once
//    and try again, with no sign-in.
{
  const w = world({ cognito: renewed });
  w.storage.setItem(KEY, JSON.stringify({ ...w.stored(), exp: T0 + 600_000 }));
  const r = await w.auth.authorized("/api/state", { method: "GET" });
  assert.equal(r.status, 200);
  assert.deepEqual(w.calls.api, ["access-1", "access-2"]);
  assert.equal(w.calls.cognito.length, 1);
  assert.ok(w.auth.signedIn());
}

// 6. A second 401 after a renewal is an error, never a sign-out.
{
  const w = world({ cognito: renewed, apiAccepts: () => false });
  const r = await w.auth.authorized("/api/state", { method: "GET" });
  assert.equal(r.status, 401);
  assert.ok(w.auth.signedIn(), "only Cognito ends the login");
}

// 7. A valid access token is used as it is, with no Cognito call.
{
  const w = world({ cognito: renewed, apiAccepts: (t) => t === "access-1" });
  w.storage.setItem(KEY, JSON.stringify({ ...w.stored(), exp: T0 + 600_000 }));
  assert.equal((await w.auth.authorized("/api/me", { method: "GET" })).status, 200);
  assert.equal(w.calls.cognito.length, 0);
}

// 8. Permanent Cognito errors end the login like a refusal: the app client
//    was replaced (ResourceNotFoundException) or the call is no longer valid.
//    A retry could never work, so the phone must not stay stuck on an error.
for (const type of ["ResourceNotFoundException", "InvalidParameterException", "UnsupportedOperationException"]) {
  const w = world({ cognito: () => Promise.resolve(response(400, { __type: type, message: "x" })) });
  assert.equal(await w.auth.accessToken(), null, type);
  assert.equal(w.stored(), null, type + " deletes the login");
}

// 9. The login ending calls onEnded once, before the login is deleted, so the
//    screen can say why and keep a chat draft. A failure to renew does not.
{
  const seen = [];
  const w = world({ cognito: refused("Refresh Token has expired") });
  const storage = w.storage;
  const auth = create({ storage, fetch: (u, i) => (u === COGNITO ? refused("x")() : null), region: "eu-north-1", clientId: "client", now: () => T0,
    onEnded: (e) => seen.push({ type: e.type, stillStored: !!storage.getItem(KEY) }) });
  assert.equal(await auth.accessToken(), null);
  assert.deepEqual(seen, [{ type: "NotAuthorizedException", stillStored: true }]);
  const w2 = world({ cognito: unreachable });
  const auth2 = create({ storage: w2.storage, fetch: () => unreachable(), region: "eu-north-1", clientId: "client", now: () => T0, onEnded: () => seen.push("bad") });
  await assert.rejects(auth2.accessToken(), (e) => e.offline === true);
  assert.equal(seen.length, 1, "a network failure does not end the login");
}

// 10. A Cognito answer that did arrive (throttling) is not called a network
//     failure; no answer at all is.
{
  const w = world({ cognito: () => Promise.resolve(response(400, { __type: "TooManyRequestsException", message: "Rate exceeded" })) });
  await assert.rejects(w.auth.accessToken(), (e) => e.offline === true && !/reach/.test(e.message) && /Rate exceeded/.test(e.message));
  const n = world({ cognito: unreachable });
  await assert.rejects(n.auth.accessToken(), (e) => /Could not reach the sign-in service/.test(e.message));
}

// 11. Signed out in another tab while the renewal ran: stays signed out, the
//     late answer does not bring the login back.
{
  let answer;
  const w = world({ cognito: () => new Promise((res) => { answer = res; }) });
  const p = w.auth.accessToken();
  w.storage.removeItem(KEY);
  answer(await renewed());
  assert.equal(await p, null);
  assert.equal(w.stored(), null, "no login brought back");
}

// 12. Signed in again (new refresh token) while an old renewal ran: the new
//     login is kept, not overwritten, and a late refusal does not delete it.
{
  for (const late of [renewed, refused("old")]) {
    let answer;
    const w = world({ cognito: () => new Promise((res) => { answer = res; }) });
    const p = w.auth.accessToken();
    const fresh = { access: "access-9", refresh: "refresh-9", exp: T0 + 600_000, email: "phone@example.test", clientId: "client" };
    w.storage.setItem(KEY, JSON.stringify(fresh));
    answer(await late());
    assert.equal(await p, "access-9");
    assert.deepEqual(w.stored(), fresh);
  }
}

// 13. A 200 answer that is not JSON (a captive portal): the login stays and
//     the failure is reported as a failed renewal, not a TypeError.
{
  const w = world({ cognito: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError("bad")) }) });
  await assert.rejects(w.auth.accessToken(), (e) => e.offline === true);
  assert.ok(w.auth.signedIn());
}

console.log("auth_check: 13 cases pass");
