• npm run test:e2e runs scripts/run-e2e.js. That script starts an isolated backend on port 8082, waits until it is ready, launches Playwright, then stops the backend.

  npm run test:e2e
    -> node scripts/run-e2e.js
       -> start FastAPI/Uvicorn at 127.0.0.1:8082
       -> poll /openapi.json until HTTP 200
       -> npx playwright test
          -> reads playwright.config.js
          -> opens Chromium headlessly
          -> runs tests/e2e/pulse-markets.spec.js
       -> stop Uvicorn server (even if tests fail)

  ## playwright.config.js

  This is Playwright’s test configuration.

  const { defineConfig } = require("@playwright/test");

  Imports Playwright’s configuration helper.

  const python = process.platform === "win32"
    ? ".\\.venv\\Scripts\\python.exe"
    : "./.venv/bin/python";

  Chooses the project virtual-environment Python executable:

  - Windows: .venv\Scripts\python.exe
  - macOS/Linux: .venv/bin/python

  testDir: "./tests/e2e",
  fullyParallel: false,
  reporter: "list",

  - Tests are found in tests/e2e.
  - fullyParallel: false means tests run one at a time. This avoids database/account-state collisions.
  - list prints each test’s result in the terminal.

  use: {
    baseURL: "http://127.0.0.1:8082",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },

  Defaults for each test:

  - page.goto("/") becomes http://127.0.0.1:8082/.
  - Chromium has no visible UI (headless).
  - A screenshot is saved only when a test fails.
  - A Playwright trace is retained on failure, useful for replaying the browser actions.

  webServer: {
    command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port 8082`,
    url: "http://127.0.0.1:8082",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,

  This is Playwright’s own server-management fallback:

  - It can start FastAPI with Uvicorn.
  - It waits up to 30 seconds for port 8082.
  - Locally, reuseExistingServer: true, so Playwright uses the server already started by run-e2e.js.
  - Port 8082 deliberately isolates tests from the normal dashboard server on 8081.

  env: {
    ...process.env,
    OLLAMA_CHAT_URL: "http://127.0.0.1:9/api/chat",
    OLLAMA_TIMEOUT_SECONDS: "1",
  },

  These make Assistant tests deterministic and fast:

  - Port 9 intentionally has no Ollama service.
  - The backend falls back to its local/saved-data response path.
  - It waits only one second before doing so.

  ## scripts/run-e2e.js

  This wrapper gives more control than Playwright’s webServer option alone.

  const root = path.resolve(__dirname, "..");

  Finds the project root from scripts/run-e2e.js.

  const server = spawn(python, [
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1", "--port", "8082"
  ], {
    cwd: root,
    env: environment,
    stdio: "inherit",
    windowsHide: true
  });

  Starts the FastAPI application as a child process.

  - cwd: root: ensures imports such as app.main:app resolve correctly.
  - stdio: "inherit": Uvicorn logs appear in your terminal.
  - windowsHide: true: no extra command window on Windows.
  - It uses the same forced-Ollama-fallback environment as the Playwright config.

  ### Readiness check

  http.get("http://127.0.0.1:8082/openapi.json", ...)

  waitForServer() repeatedly requests FastAPI’s generated OpenAPI document.

  GET /openapi.json
    HTTP 200 -> backend is ready -> start browser tests
    error/non-200 -> wait 500 ms -> try again
    too many retries -> fail with “test server did not start”

  Using /openapi.json confirms FastAPI has actually loaded, rather than merely checking that the port exists.

  ### Starting Playwright

  spawn("cmd.exe", ["/d", "/s", "/c", "npx playwright test"], ...)

  On Windows it invokes Playwright through cmd.exe; on other systems it invokes npx directly. It waits for Playwright’s exit code and passes that exit code back to npm.

  So:

  - all tests pass → npm run test:e2e exits 0
  - any test fails → it exits non-zero

  ### Guaranteed cleanup

  finally {
    server.kill();
  }

  The server is terminated whether tests pass, fail, or throw an error. This prevents stale test servers from accumulating on port 8082.

  ## What the tests actually exercise

  tests/e2e/pulse-markets.spec.js performs four real browser workflows:

  1. New customer registration and sign-in; verifies the customer only sees the Market dashboard, not Management.
  2. Buy 2 AAPL, sell 2 AAPL, then verify both history records.
  3. Ask the Assistant a portfolio question; confirm the saved chat appears and can be reopened.
  4. Sign in as the master account; verify it is marked protected and its deletion API returns 403.

  One small architectural note: both run-e2e.js and playwright.config.js know how to start the server. In normal local usage, the wrapper starts it and Playwright reuses it; that is why
  their port and environment variables must stay identical.