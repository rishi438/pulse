const { defineConfig } = require("@playwright/test");

const python = process.platform === "win32" ? ".\\.venv\\Scripts\\python.exe" : "./.venv/bin/python";

module.exports = defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8082",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port 8082`,
    url: "http://127.0.0.1:8082",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      ...process.env,
      OLLAMA_CHAT_URL: "http://127.0.0.1:9/api/chat",
      OLLAMA_TIMEOUT_SECONDS: "1",
    },
  },
});
