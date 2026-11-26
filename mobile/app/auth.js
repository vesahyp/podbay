// The phone app's sign-in (Cognito USER_AUTH with EMAIL_OTP, no SDK). The
// login is the refresh token in localStorage; the access token lives 15
// minutes and is renewed from it. Loaded by index.html before app.js;
// scripts/auth_check.mjs tests it in Node.
//
// The rule that keeps a phone signed in: the login is deleted only when
// Cognito itself refuses the refresh token (expired after the client's
// refresh_token_validity, revoked by sign-out, user removed). A refresh that
// fails for any other reason (no network while iOS wakes the app, a
// page reload that cuts the request, Cognito throttling or down) keeps the
// login and the next call tries again. Before 2026-11-26 any failure deleted
// it, and the phone asked for a new sign-in a few times a week.
(function (root) {
  "use strict";

  var KEY = "podbay.auth";
  // Cognito's answers that mean the refresh token cannot be used again.
  var REFUSED = { NotAuthorizedException: 1, UserNotFoundException: 1, RefreshTokenReuseException: 1 };

  function create(opts) {
    var storage = opts.storage;
    var send = opts.fetch;
    var now = opts.now || function () { return Date.now(); };
    var region = opts.region || "eu-north-1";
    var endpoint = "https://cognito-idp." + region + ".amazonaws.com/";
    var refreshing = null;

    function load() {
      try { return JSON.parse(storage.getItem(KEY) || "null"); } catch (e) { return null; }
    }
    function save(a) {
      try { if (a) storage.setItem(KEY, JSON.stringify(a)); else storage.removeItem(KEY); } catch (e) { /* private mode */ }
    }
    function signedIn() { return !!(load() || {}).refresh; }

    // One Cognito call. A rejection carries .type (Cognito's __type) when
    // Cognito answered, and no .type when the request did not get through.
    function cognito(target, body) {
      return send(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSCognitoIdentityProviderService." + target },
        body: JSON.stringify(body),
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) { var e = new Error(j.message || j.__type || ("HTTP " + r.status)); e.type = j.__type || ""; e.status = r.status; throw e; }
          return j;
        });
      });
    }

    function store(res, email, clientId, old) {
      var a = {
        access: res.AccessToken,
        refresh: res.RefreshToken || (old && old.refresh),
        exp: now() + (res.ExpiresIn || 900) * 1000,
        email: email,
        clientId: clientId,
      };
      save(a);
      return a;
    }

    // The access token, renewed when it has less than a minute left or when
    // force is set (the API answered 401). Resolves null when signed out;
    // rejects with .offline when the renewal did not get an answer, and the
    // login stays for the next try.
    function accessToken(force) {
      var a = load();
      if (!a || !a.refresh) return Promise.resolve(null);
      if (!force && a.access && a.exp - 60000 > now()) return Promise.resolve(a.access);
      if (!refreshing) {
        var clientId = a.clientId || opts.clientId;
        refreshing = cognito("InitiateAuth", {
          AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: clientId, AuthParameters: { REFRESH_TOKEN: a.refresh },
        }).then(function (res) {
          // Another tab may have signed out meanwhile: keep it signed out.
          var current = load();
          if (!current || current.refresh !== a.refresh) return current && current.access && current.exp - 60000 > now() ? current.access : null;
          return store(res.AuthenticationResult, a.email, clientId, a).access;
        }, function (e) {
          if (REFUSED[e.type]) {
            var current = load();
            if (current && current.refresh === a.refresh) save(null);
            return null;
          }
          var err = new Error("Could not reach the sign-in service: " + e.message);
          err.offline = true;
          throw err;
        });
        var clear = function () { refreshing = null; };
        refreshing.then(clear, clear);
      }
      return refreshing;
    }

    function signOut() {
      var a = load();
      save(null);
      if (a && a.refresh) cognito("RevokeToken", { ClientId: a.clientId || opts.clientId, Token: a.refresh }).catch(function () {});
    }

    // fetch() with the access token. A 401 renews the token once and tries
    // again; a second 401 is an error, never a sign-out: only Cognito
    // decides that the login is over.
    function authorized(url, init) {
      function attempt(force) {
        return accessToken(force).then(function (token) {
          if (!token) { var e = new Error("signed out"); e.status = 401; e.signedOut = true; throw e; }
          var headers = Object.assign({}, (init && init.headers) || {}, { Authorization: "Bearer " + token });
          return send(url, Object.assign({}, init || {}, { headers: headers }));
        });
      }
      return attempt(false).then(function (r) { return r.status === 401 ? attempt(true) : r; });
    }

    return { KEY: KEY, load: load, save: save, signedIn: signedIn, cognito: cognito, store: store,
      accessToken: accessToken, authorized: authorized, signOut: signOut };
  }

  var api = { create: create, KEY: KEY };
  root.PodbayAuth = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
