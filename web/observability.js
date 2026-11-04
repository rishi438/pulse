(function installPulseTelemetry(window) {
  "use strict";

  if (Object.prototype.hasOwnProperty.call(window, "PulseTelemetry")) {
    return;
  }

  const originalFetch = window.fetch;
  const counters = { accepted: 0, failed: 0, dropped: 0, attempted: 0 };
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const groups = new Set(["auth", "portfolio", "quotes", "orders", "assistant", "admin", "roles", "users"]);
  const methods = new Set(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]);
  const errorTypes = new Set(["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "URIError", "EvalError", "NetworkError", "AbortError"]);
  const xhrStates = new WeakMap();
  let intervalStart = Date.now();
  let intervalCount = 0;

  Object.defineProperty(window, "PulseTelemetry", {
    value: Object.freeze({ stats: () => Object.freeze({ ...counters }) }),
    writable: false,
    configurable: false,
  });

  function validId(value) {
    return typeof value === "string" && uuidPattern.test(value) ? value.toLowerCase() : undefined;
  }

  function apiRoute(input) {
    let address;
    if (typeof input === "string") {
      address = input;
    } else if (window.Request && input instanceof window.Request) {
      address = input.url;
    } else if (input instanceof window.URL) {
      address = input.href;
    } else {
      return null;
    }
    const url = new window.URL(address, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/") || url.pathname === "/api/client-events" || url.pathname === "/api/client-events/") {
      return null;
    }
    const segment = url.pathname.split("/")[2];
    return { route_group: groups.has(segment) ? segment : "unknown" };
  }

  function methodName(value) {
    const method = typeof value === "string" ? value.toUpperCase() : "OTHER";
    return methods.has(method) ? method : "OTHER";
  }

  function errorType(error) {
    try {
      return error && errorTypes.has(error.name) ? error.name : "Other";
    } catch {
      // Arbitrary rejection objects may have throwing accessors; never inspect further.
      return "Other";
    }
  }

  function now() {
    return window.performance.now();
  }

  function elapsed(start) {
    return Math.min(600000, Math.max(0, now() - start));
  }

  function statusCode(value) {
    return Number.isInteger(value) && value >= 0 && value <= 599 ? value : 0;
  }

  // Detached telemetry owns its rejection and timeout. It never retries or reports itself.
  function report(payload) {
    counters.attempted += 1;
    let timeout;
    try {
      const current = Date.now();
      if (current - intervalStart >= 60000 || current < intervalStart) {
        intervalStart = current;
        intervalCount = 0;
      }
      const body = JSON.stringify(payload);
      if (intervalCount >= 20 || new window.TextEncoder().encode(body).byteLength > 2048 || typeof originalFetch !== "function") {
        counters.dropped += 1;
        return;
      }
      intervalCount += 1;
      const controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
      if (controller) {
        timeout = window.setTimeout(() => controller.abort(), 5000);
      }
      const request = originalFetch.call(window, "/api/client-events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        keepalive: true,
        body,
        ...(controller ? { signal: controller.signal } : {}),
      });
      Promise.resolve(request).then(
        (response) => {
          if (response.status >= 200 && response.status < 300) {
            counters.accepted += 1;
          } else {
            counters.failed += 1;
          }
        },
        () => { counters.failed += 1; },
      ).finally(() => {
        if (timeout !== undefined) { window.clearTimeout(timeout); }
      });
    } catch {
      // An unavailable browser API or synchronous transport failure must not break the app.
      if (timeout !== undefined) { window.clearTimeout(timeout); }
      counters.failed += 1;
    }
  }

  function recordApi(metadata, start, status, responseId, error) {
    const duration = elapsed(start);
    const failed = status < 200 || status >= 300;
    if (!failed && duration < 1000) {
      return;
    }
    const requestId = validId(responseId) || validId(metadata.request_id);
    report({
      event: failed ? "api_error" : "api_slow",
      transport: metadata.transport,
      route_group: metadata.route_group,
      method: metadata.method,
      status,
      duration_ms: duration,
      timing_scope: metadata.transport === "fetch" ? "headers" : "response",
      ...(requestId ? { request_id: requestId } : {}),
      ...(error ? { error_type: errorType(error) } : {}),
    });
  }

  if (typeof originalFetch === "function") {
    window.fetch = async function instrumentedFetch(input, init) {
      let metadata;
      try {
        const route = apiRoute(input);
        if (route) {
          const request = window.Request && input instanceof window.Request ? input : null;
          const headers = new window.Headers(init && init.headers !== undefined ? init.headers : request ? request.headers : undefined);
          const requestId = validId(headers.get("X-Request-ID"));
          metadata = { ...route, transport: "fetch", method: methodName(init && init.method !== undefined ? init.method : request ? request.method : "GET"), request_id: requestId };
        }
      } catch {
        // Invalid or unsupported input remains native fetch's responsibility.
        counters.dropped += 1;
        return originalFetch.call(this, input, init);
      }
      if (!metadata) {
        return originalFetch.call(this, input, init);
      }
      const start = now();
      return originalFetch.call(this, input, init).then(
        (response) => {
          recordApi(metadata, start, statusCode(response.status), response.headers.get("X-Request-ID"));
          return response;
        },
        (error) => {
          recordApi(metadata, start, 0, undefined, error);
          throw error;
        },
      );
    };
  }

  if (window.XMLHttpRequest) {
    const prototype = window.XMLHttpRequest.prototype;
    const originalOpen = prototype.open;
    const originalSend = prototype.send;
    const originalSetHeader = prototype.setRequestHeader;

    prototype.open = function instrumentedOpen(method, url) {
      const result = originalOpen.apply(this, arguments);
      let route;
      try { route = apiRoute(url); } catch { route = null; }
      xhrStates.set(this, route ? { ...route, transport: "xhr", method: methodName(method), hasHeader: false } : null);
      return result;
    };

    prototype.setRequestHeader = function instrumentedSetHeader(name, value) {
      const result = originalSetHeader.apply(this, arguments);
      const metadata = xhrStates.get(this);
      if (metadata) {
        if (typeof name !== "string") {
          // Do not coerce caller objects twice or risk appending a duplicate header.
          metadata.hasHeader = true;
          metadata.request_id = undefined;
        } else if (name.toLowerCase() === "x-request-id") {
          metadata.request_id = metadata.hasHeader ? undefined : validId(value);
          metadata.hasHeader = true;
        }
      }
      return result;
    };

    prototype.send = function instrumentedSend() {
      const metadata = xhrStates.get(this);
      if (!metadata) {
        return originalSend.apply(this, arguments);
      }
      const start = now();
      let failure;
      const onError = () => { failure = { name: "NetworkError" }; };
      const onAbort = () => { failure = { name: "AbortError" }; };
      const cleanup = () => {
        this.removeEventListener("error", onError);
        this.removeEventListener("timeout", onError);
        this.removeEventListener("abort", onAbort);
        this.removeEventListener("loadend", onEnd);
      };
      const onEnd = () => {
        cleanup();
        recordApi(metadata, start, statusCode(this.status), this.getResponseHeader("X-Request-ID"), failure);
      };
      this.addEventListener("error", onError);
      this.addEventListener("timeout", onError);
      this.addEventListener("abort", onAbort);
      this.addEventListener("loadend", onEnd);
      try {
        return originalSend.apply(this, arguments);
      } catch (error) {
        cleanup();
        recordApi(metadata, start, 0, undefined, error);
        throw error;
      }
    };
  }

  window.addEventListener("error", (event) => {
    if (event.target && event.target !== window) {
      report({ event: "resource_error", transport: "browser", route_group: "unknown", timing_scope: "event" });
      return;
    }
    const payload = { event: "js_error", transport: "browser", route_group: "unknown", timing_scope: "event", error_type: errorType(event.error) };
    if (Number.isInteger(event.lineno) && event.lineno >= 0 && event.lineno <= 10000000) { payload.line = event.lineno; }
    if (Number.isInteger(event.colno) && event.colno >= 0 && event.colno <= 10000000) { payload.column = event.colno; }
    report(payload);
  }, true);

  window.addEventListener("unhandledrejection", (event) => {
    report({ event: "promise_rejection", transport: "browser", route_group: "unknown", timing_scope: "event", error_type: errorType(event.reason) });
  });
})(window);
