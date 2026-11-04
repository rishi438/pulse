"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "..", "web", "observability.js"), "utf8");
const generatedId = "4c946be2-f344-4c53-9cf2-f10bf35b6801";
const serverId = "7e11d850-e851-4511-bad8-c29b798e35ba";
const callerId = "8bbca60b-f465-497e-af87-fd085618d10a";
const secret = "SENSITIVE_email-token-query-message-body-filename";

function response(status, id = null) {
  return {
    status,
    headers: new Headers(id ? { "X-Request-ID": id } : {}),
    get body() { throw new Error("Response body must not be read"); },
    clone() { throw new Error("Response must not be cloned"); },
    json() { throw new Error("Response JSON must not be read"); },
    text() { throw new Error("Response text must not be read"); },
  };
}

function harness() {
  let time = 0;
  let wallTime = 0;
  let business = () => Promise.resolve(response(200));
  let telemetry = () => Promise.resolve(response(204));
  const calls = [];
  const reports = [];
  const listeners = new Map();
  const timers = new Map();
  let timerId = 0;

  class FakeXHR {
    constructor() {
      this.listeners = new Map();
      this.headers = new Map();
      this.status = 0;
      this.responseHeaders = new Headers();
      this.sent = false;
    }
    open(...args) { this.openArgs = args; this.sent = false; this.headers = new Map(); }
    setRequestHeader(name, value) {
      if (this.sent) { throw new Error("XHR already sent"); }
      const normalized = String(name).toLowerCase();
      this.headers.set(normalized, this.headers.has(normalized) ? `${this.headers.get(normalized)}, ${String(value)}` : String(value));
    }
    send(...args) {
      if (this.sent) { throw new Error("XHR already sent"); }
      this.sent = true;
      this.sendArgs = args;
      if (this.sendFailure) { throw this.sendFailure; }
      return this.returnValue;
    }
    getResponseHeader(name) { return this.responseHeaders.get(name); }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) { this.listeners.set(type, new Set()); }
      this.listeners.get(type).add(listener);
    }
    removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
    emit(type) { for (const listener of [...(this.listeners.get(type) || [])]) { listener.call(this, { type }); } }
    finish(status, id, error) {
      this.status = status;
      this.responseHeaders = new Headers(id ? { "X-Request-ID": id } : {});
      if (error) { this.emit(error); }
      this.emit("loadend");
    }
    get responseText() { throw new Error("XHR response must not be read"); }
    get response() { throw new Error("XHR response must not be read"); }
  }

  const originalFetch = function (input, init) {
    if (input === "/api/client-events") {
      reports.push(JSON.parse(init.body));
      calls.push({ input, init, telemetry: true, thisValue: this });
      return telemetry(input, init);
    }
    calls.push({ input, init, telemetry: false, thisValue: this });
    return business(input, init);
  };
  const window = {
    fetch: originalFetch,
    XMLHttpRequest: FakeXHR,
    Headers,
    Request,
    URL,
    TextEncoder,
    AbortController,
    location: { origin: "http://localhost:8081", href: "http://localhost:8081/dashboard" },
    performance: { now: () => time },
    crypto: { randomUUID: () => generatedId },
    setTimeout: (callback) => { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    addEventListener: (name, listener) => {
      if (!listeners.has(name)) { listeners.set(name, []); }
      listeners.get(name).push(listener);
    },
    get localStorage() { throw new Error("Storage must not be read"); },
    get document() { throw new Error("Cookies/document must not be read"); },
  };
  class ClockDate extends Date { static now() { return wallTime; } }
  const context = vm.createContext({ window, Date: ClockDate });
  const install = () => vm.runInContext(source, context, { filename: "observability.js" });
  install();
  return {
    window, reports, calls, timers, originalFetch, install,
    setBusiness: (callback) => { business = callback; },
    setTelemetry: (callback) => { telemetry = callback; },
    advance: (milliseconds) => { time += milliseconds; wallTime += milliseconds; },
    emit: (name, event) => { for (const listener of listeners.get(name) || []) { listener(event); } },
    flush: () => new Promise((resolve) => setImmediate(resolve)),
  };
}

test("fetch records slow headers without reading bodies and keeps Response identity", async () => {
  const h = harness();
  const result = response(200, serverId.toUpperCase());
  h.setBusiness(() => { h.advance(1200); return Promise.resolve(result); });
  assert.equal(await h.window.fetch(`/api/portfolio?token=${secret}`), result);
  await h.flush();
  assert.equal(h.calls[0].init, undefined);
  assert.deepEqual(h.reports[0], { event: "api_slow", transport: "fetch", route_group: "portfolio", method: "GET", status: 200, duration_ms: 1200, timing_scope: "headers", request_id: serverId });
  assert.equal(JSON.stringify(h.reports).includes(secret), false);
  assert.equal(h.window.PulseTelemetry.stats().accepted, 1);
});

test("fast fetch success does not report and business headers/body remain intact", async () => {
  const h = harness();
  const init = { method: "POST", headers: { Authorization: secret, "Content-Type": "application/json", "X-Request-ID": callerId }, body: secret, credentials: "include" };
  await h.window.fetch("/api/orders", init);
  const actual = h.calls[0].init;
  assert.equal(new Headers(actual.headers).get("Authorization"), secret);
  assert.equal(new Headers(actual.headers).get("Content-Type"), "application/json");
  assert.equal(new Headers(actual.headers).get("X-Request-ID"), callerId);
  assert.equal(actual.body, secret);
  assert.equal(actual.credentials, "include");
  assert.equal(init.headers["X-Request-ID"], callerId);
  assert.equal(h.reports.length, 0);
});

test("fetch non2xx reports safe group and leaves invalid caller header untouched", async () => {
  const h = harness();
  h.setBusiness(() => Promise.resolve(response(500, "invalid-server-id")));
  await h.window.fetch(`/api/${secret}/private?password=${secret}`, { method: "CUSTOM", headers: { "X-Request-ID": secret } });
  await h.flush();
  assert.equal(h.calls[0].init.headers["X-Request-ID"], secret);
  assert.deepEqual(h.reports[0], { event: "api_error", transport: "fetch", route_group: "unknown", method: "OTHER", status: 500, duration_ms: 0, timing_scope: "headers" });
  assert.equal(JSON.stringify(h.reports).includes(secret), false);
});

test("fetch network rejection propagates original error and reports only error type", async () => {
  const h = harness();
  const error = new TypeError(secret);
  h.setBusiness(() => Promise.reject(error));
  await assert.rejects(h.window.fetch("/api/assistant"), (actual) => actual === error);
  await h.flush();
  assert.equal(h.reports[0].error_type, "TypeError");
  assert.equal(h.reports[0].status, 0);
  assert.equal(JSON.stringify(h.reports).includes(secret), false);
});

test("external and non-api fetch bypass instrumentation with exact native arguments/this", async () => {
  const h = harness();
  for (const url of ["https://outside.example/api/orders", "/app.js", "/api", "/api/client-events", "/api/client-events/"]) {
    const init = { headers: { Authorization: secret } };
    const receiver = {};
    if (url === "/api/client-events") { init.body = "{}"; }
    await h.window.fetch.call(receiver, url, init);
    const call = h.calls.at(-1);
    assert.equal(call.init, init);
    assert.equal(call.thisValue, receiver);
    assert.equal(new Headers(call.init.headers).has("X-Request-ID"), false);
  }
});

test("Request plus init retains overrides, inherited/nonenumerable options, signal and body state", async () => {
  const h = harness();
  const controller = new AbortController();
  const request = new Request("http://localhost:8081/api/orders", { method: "POST", headers: { "X-Original": "original", "X-Request-ID": callerId }, body: secret, duplex: "half" });
  const init = Object.create({ credentials: "include", method: "PUT", signal: controller.signal });
  Object.defineProperty(init, "headers", { value: { "X-Override": "override" } });
  Object.defineProperty(init, "body", { value: "override-body" });
  await h.window.fetch(request, init);
  const call = h.calls[0];
  assert.equal(call.input, request);
  assert.equal(request.bodyUsed, false);
  const effective = new Request(call.input, call.init);
  assert.equal(effective.method, "PUT");
  assert.equal(effective.credentials, "include");
  assert.equal(effective.headers.has("X-Original"), false);
  assert.equal(effective.headers.get("X-Override"), "override");
  assert.equal(effective.headers.has("X-Request-ID"), false);
  assert.equal(call.init, init);
  assert.equal(await effective.text(), "override-body");
  controller.abort();
  assert.equal(effective.signal.aborted, true);
});

test("Request headers/method are inherited when init does not override them", async () => {
  const h = harness();
  h.setBusiness(() => Promise.resolve(response(409)));
  const request = new Request("http://localhost:8081/api/orders", { method: "DELETE", headers: { Authorization: secret, "X-Request-ID": callerId } });
  await h.window.fetch(request);
  const effective = new Request(h.calls[0].input, h.calls[0].init);
  assert.equal(effective.method, "DELETE");
  assert.equal(effective.headers.get("Authorization"), secret);
  assert.equal(effective.headers.get("X-Request-ID"), callerId);
  assert.equal(h.reports[0].method, "DELETE");
});

test("XHR slow response includes response time and leaves jQuery business headers/body untouched", async () => {
  const h = harness();
  const xhr = new h.window.XMLHttpRequest();
  xhr.open("GET", `/api/admin/users?email=${secret}`, true, "username", secret);
  xhr.setRequestHeader("Authorization", secret);
  xhr.send(secret);
  assert.equal(xhr.headers.has("x-request-id"), false);
  assert.equal(xhr.headers.get("authorization"), secret);
  assert.deepEqual(xhr.sendArgs, [secret]);
  h.advance(1500);
  xhr.finish(200, serverId);
  await h.flush();
  assert.deepEqual(h.reports[0], { event: "api_slow", transport: "xhr", route_group: "admin", method: "GET", status: 200, duration_ms: 1500, timing_scope: "response", request_id: serverId });
  assert.equal(JSON.stringify(h.reports).includes(secret), false);
  assert.equal([...xhr.listeners.values()].every((entries) => entries.size === 0), true);
});

test("XHR failures retain valid caller ID and do not duplicate invalid supplied ID", async () => {
  const h = harness();
  for (const id of [callerId, secret]) {
    const xhr = new h.window.XMLHttpRequest();
    xhr.open("POST", "/api/orders");
    xhr.setRequestHeader("x-request-id", id);
    xhr.send();
    assert.equal(xhr.headers.get("x-request-id"), id);
    xhr.finish(503, id === secret ? serverId : null);
  }
  await h.flush();
  assert.equal(h.reports[0].request_id, callerId);
  assert.equal(h.reports[1].request_id, serverId);
});

test("XHR network/abort errors are metadata only and external requests bypass", async () => {
  const h = harness();
  for (const event of ["error", "abort", "timeout"]) {
    const xhr = new h.window.XMLHttpRequest();
    xhr.open("GET", "/api/quotes");
    xhr.send();
    xhr.finish(0, null, event);
  }
  const external = new h.window.XMLHttpRequest();
  external.open("POST", "https://external.example/api/orders");
  external.send(secret);
  external.finish(500);
  const asset = new h.window.XMLHttpRequest();
  asset.open("GET", "/app.js");
  asset.send();
  asset.finish(500);
  await h.flush();
  assert.equal(h.reports.length, 3);
  assert.deepEqual(h.reports.map((entry) => entry.error_type), ["NetworkError", "AbortError", "NetworkError"]);
  assert.equal(external.headers.has("x-request-id"), false);
  assert.equal(asset.headers.has("x-request-id"), false);
});

test("XHR fast success does not report and reused objects reset correlation", async () => {
  const h = harness();
  const xhr = new h.window.XMLHttpRequest();
  xhr.open("GET", "/api/users");
  xhr.setRequestHeader("X-Request-ID", callerId);
  xhr.send();
  xhr.finish(200);
  assert.equal(h.reports.length, 0);
  xhr.open("DELETE", "/api/users/123");
  xhr.send();
  xhr.finish(404);
  await h.flush();
  assert.equal(h.reports[0].request_id, undefined);
  assert.equal(h.reports[0].method, "DELETE");
});

test("XHR synchronous send failure preserves error identity and cleans listeners", async () => {
  const h = harness();
  const xhr = new h.window.XMLHttpRequest();
  xhr.open("POST", "/api/orders");
  xhr.sendFailure = new TypeError(secret);
  assert.throws(() => xhr.send(secret), (error) => error === xhr.sendFailure);
  await h.flush();
  assert.equal(h.reports[0].error_type, "TypeError");
  assert.equal([...xhr.listeners.values()].every((entries) => entries.size === 0), true);
});

test("JS, resource and rejection events never inspect raw message/stack/filename", async () => {
  const h = harness();
  const error = new Error(secret);
  for (const property of ["message", "stack", "filename"]) {
    Object.defineProperty(error, property, { get() { throw new Error("Sensitive error property was read"); } });
  }
  h.emit("error", { target: h.window, error, lineno: 42, colno: 7, filename: secret, message: secret });
  h.emit("error", { target: { src: secret }, error, filename: secret });
  h.emit("unhandledrejection", { reason: secret });
  h.emit("unhandledrejection", { reason: { name: secret, message: secret, stack: secret } });
  h.emit("unhandledrejection", { reason: Object.defineProperty({}, "name", { get() { throw error; } }) });
  await h.flush();
  assert.deepEqual(h.reports.map((entry) => entry.event), ["js_error", "resource_error", "promise_rejection", "promise_rejection", "promise_rejection"]);
  assert.equal(h.reports[0].line, 42);
  assert.equal(h.reports[0].column, 7);
  assert.equal(h.reports[0].error_type, "Error");
  assert.equal(h.reports[2].error_type, "Other");
  assert.equal(JSON.stringify(h.reports).includes(secret), false);
  assert.equal(h.reports.every((entry) => new TextEncoder().encode(JSON.stringify(entry)).byteLength <= 2048), true);
});

test("reports are bounded at 20/minute and telemetry uses no auth, recursion or body reads", async () => {
  const h = harness();
  h.setTelemetry(() => Promise.reject(new Error(secret)));
  for (let index = 0; index < 25; index += 1) { h.emit("unhandledrejection", { reason: secret }); }
  await h.flush();
  assert.equal(h.reports.length, 20);
  assert.deepEqual({ ...h.window.PulseTelemetry.stats() }, { accepted: 0, failed: 20, dropped: 5, attempted: 25 });
  for (const call of h.calls) {
    assert.equal(call.input, "/api/client-events");
    assert.deepEqual(Object.keys(call.init.headers), ["Content-Type"]);
    assert.equal(call.init.credentials, "same-origin");
    assert.equal(call.init.keepalive, true);
    assert.equal(call.thisValue, h.window);
  }
  assert.equal(h.timers.size, 0);
  h.advance(60000);
  h.emit("unhandledrejection", { reason: secret });
  await h.flush();
  assert.equal(h.reports.length, 21);
});

test("telemetry non2xx and synchronous failures count once without rejection recursion", async () => {
  const h = harness();
  h.setTelemetry(() => Promise.resolve(response(429)));
  h.emit("unhandledrejection", { reason: secret });
  await h.flush();
  h.setTelemetry(() => { throw new Error(secret); });
  h.emit("unhandledrejection", { reason: secret });
  await h.flush();
  assert.deepEqual({ ...h.window.PulseTelemetry.stats() }, { accepted: 0, failed: 2, dropped: 0, attempted: 2 });
  assert.equal(h.timers.size, 0);
});

test("double loading is idempotent and stats are a frozen read-only snapshot", async () => {
  const h = harness();
  const fetch = h.window.fetch;
  const send = h.window.XMLHttpRequest.prototype.send;
  const before = h.window.PulseTelemetry.stats();
  h.install();
  assert.equal(h.window.fetch, fetch);
  assert.equal(h.window.XMLHttpRequest.prototype.send, send);
  assert.equal(Object.isFrozen(before), true);
  assert.equal(Object.isFrozen(h.window.PulseTelemetry), true);
  assert.throws(() => { h.window.PulseTelemetry = {}; }, TypeError);
  assert.throws(() => { before.failed = 200; }, TypeError);
  h.emit("unhandledrejection", { reason: secret });
  await h.flush();
  assert.equal(h.reports.length, 1);
  assert.equal(before.accepted, 0);
  assert.deepEqual(Object.keys(h.window.PulseTelemetry.stats()).sort(), ["accepted", "attempted", "dropped", "failed"]);
});
