const { test, expect } = require("@playwright/test");

const password = process.env.PULSE_E2E_PASSWORD;
const masterEmail = process.env.PULSE_MASTER_EMAIL;
const masterPassword = process.env.PULSE_MASTER_PASSWORD;

function customerEmail() {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@pulse.local`;
}

async function registerAndSignIn(page, email = customerEmail()) {
  await page.goto("/");
  await page.getByRole("button", { name: "New customer? Register" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Register", exact: true }).click();
  await expect(page.getByText("Customer account created. Sign in now.")).toBeVisible();
  await page.getByRole("button", { name: "Already registered? Sign in" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Market dashboard" })).toBeVisible();
  return email;
}

async function signIn(page, email, accountPassword) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(accountPassword);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("customer registration, sign-in, and dashboard-only routing", async ({ page }) => {
  await registerAndSignIn(page);

  await expect(page.getByRole("heading", { name: "Your market, in motion." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Management" })).toHaveCount(0);

  await page.goto("/#management");
  await expect(page.getByRole("heading", { name: "Your market, in motion." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Administration" })).toHaveCount(0);
});

test("customer can buy, sell, and see both transactions", async ({ page }) => {
  await registerAndSignIn(page);
  await page.getByRole("button", { name: "Trade" }).click();
  await expect(page.getByRole("heading", { name: "Place a paper trade" })).toBeVisible();
  await page.getByLabel("Shares").fill("2");

  await page.getByRole("button", { name: "Buy", exact: true }).click();
  await expect(page.getByText(/Bought 2 AAPL at \$/)).toBeVisible();
  await expect(page.locator(".portfolio-card").getByText("AAPL", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Sell", exact: true }).click();
  await expect(page.getByText(/Sold 2 AAPL at \$/)).toBeVisible();

  await page.getByRole("button", { name: "History" }).click();
  await expect(page.getByRole("heading", { name: "Transaction history", level: 1 })).toBeVisible();
  await expect(page.getByText("SELL AAPL")).toBeVisible();
  await expect(page.getByText("BUY AAPL")).toBeVisible();
});

test("Assistant saves a chat and restores it from the sidebar", async ({ page }) => {
  test.setTimeout(120_000);
  await registerAndSignIn(page);
  await page.getByRole("button", { name: "Assistant" }).click();
  await expect(page.getByText("Your saved chats will appear here.")).toBeVisible();

  const question = "What is my current portfolio summary?";
  await page.getByPlaceholder("Ask a question or describe the analysis you want").fill(question);
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Reviewing your saved dataâ€¦")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.locator(".assistant-message.assistant")).toHaveCount(2);
  await expect(page.locator(".assistant-performance")).toHaveCount(1);
  await expect(page.getByText("Initial funding")).toBeVisible();
  await expect(page.locator(".assistant-chat-link")).toHaveCount(1);
  await expect(page.locator(".assistant-chat-link").first()).toContainText(question);

  await page.getByRole("button", { name: "+ New" }).click();
  await expect(page.getByRole("heading", { name: "New conversation" })).toBeVisible();
  await page.locator(".assistant-chat-link").first().click();
  await expect(page.getByText(question)).toBeVisible();
});

test("protected master account is marked in Management and cannot be deleted", async ({ page }) => {
  await signIn(page, masterEmail, masterPassword);
  await expect(page.getByRole("button", { name: "Management" })).toBeVisible();
  await page.getByRole("button", { name: "Management" }).click();
  await page.getByRole("button", { name: "User management" }).click();

  await expect(page.getByText(`${masterEmail} is permanently protected.`)).toBeVisible();

  const deleteStatus = await page.evaluate(async protectedEmail => {
    const token = localStorage.getItem("pulse-token");
    const overview = await fetch("/api/admin/overview", { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
    const master = overview.users.find(user => user.email === protectedEmail);
    return fetch(`/api/users/${master.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }).then(response => response.status);
  }, masterEmail);
  expect(deleteStatus).toBe(403);
});
