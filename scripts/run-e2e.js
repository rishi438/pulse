const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const http = require("node:http");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const python = process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python");
const environment = {
  ...process.env,
  OLLAMA_CHAT_URL: "http://127.0.0.1:9/api/chat",
  OLLAMA_TIMEOUT_SECONDS: "1",
  PULSE_E2E_PASSWORD: process.env.PULSE_E2E_PASSWORD || crypto.randomBytes(24).toString("base64url"),
  PULSE_MASTER_EMAIL: process.env.PULSE_MASTER_EMAIL || "e2e-master@pulse.local",
  PULSE_MASTER_PASSWORD: process.env.PULSE_MASTER_PASSWORD || crypto.randomBytes(24).toString("base64url"),
  PULSE_RESET_BOOTSTRAP_PASSWORDS: "true",
};
const server = spawn(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8082"], { cwd: root, env: environment, stdio: "inherit", windowsHide: true });

function waitForServer(remaining = 20) {
  return new Promise((resolve, reject) => {
    const request = http.get("http://127.0.0.1:8082/openapi.json", response => {
      response.resume();
      response.statusCode === 200 ? resolve() : retry();
    });
    request.on("error", retry);
    request.setTimeout(1_000, () => request.destroy());
    function retry() {
      if (remaining <= 0) return reject(new Error("Pulse Markets test server did not start"));
      setTimeout(() => waitForServer(remaining - 1).then(resolve, reject), 500);
    }
  });
}

async function run() {
  try {
    await waitForServer();
    const windows = process.platform === "win32";
    const tests = windows
      ? spawn("cmd.exe", ["/d", "/s", "/c", "npx playwright test"], { cwd: root, env: environment, stdio: "inherit", windowsHide: true })
      : spawn("npx", ["playwright", "test"], { cwd: root, env: environment, stdio: "inherit" });
    const exitCode = await new Promise(resolve => tests.on("exit", code => resolve(code ?? 1)));
    process.exitCode = exitCode;
  } finally {
    server.kill();
  }
}

server.on("error", error => { throw error; });
run();
