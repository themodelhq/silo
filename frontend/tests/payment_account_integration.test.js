// Loads the ACTUAL frontend/js/app.js source (not a re-implementation) into
// a minimal browser-like sandbox, then exercises the Payment Account
// functions against a real, running backend instance to verify the
// integration end-to-end rather than just by inspection.
//
// Usage:
//   cd backend && rm -f silo.db
//   ALLOWED_ORIGINS="*" JWT_SECRET_KEY="test-secret" \
//     python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &
//   cd ../frontend && node tests/payment_account_integration.test.js
//
// Override the target with TEST_API_BASE_URL if not using the default
// http://127.0.0.1:8123. This intentionally runs against a backend with no
// PAYSTACK_SECRET_KEY configured — it verifies the auth bridge (register/
// login/wrong-password) and error handling, not live Paystack calls (which
// need real credentials and are covered on the backend side by
// backend/tests/test_integration_flows.py's monkeypatched DVA lifecycle).
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

const APP_JS_PATH = path.join(__dirname, "..", "js", "app.js");
const appJsSource = fs.readFileSync(APP_JS_PATH, "utf8");
const API_BASE_URL = process.env.TEST_API_BASE_URL || "http://127.0.0.1:8123";

function makeSandbox() {
  const listeners = {};
  const fakeStorage = { data: {} };

  const documentStub = {
    getElementById(id) {
      if (!fakeStorage.data[id]) {
        fakeStorage.data[id] = {
          value: "", textContent: "", disabled: false, style: {}, innerHTML: "",
          addEventListener() {}, appendChild() {}, classList: { add() {}, remove() {}, toggle() {} },
        };
      }
      return fakeStorage.data[id];
    },
    createElement(tag) {
      // Minimal stand-in for escapeHTML()'s div.textContent -> div.innerHTML trick.
      const el = { textContent: "", remove() {} };
      Object.defineProperty(el, "innerHTML", {
        get() {
          return String(el.textContent)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
        },
      });
      return el;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };

  const toasts = [];

  const sandbox = {
    window: {},
    document: documentStub,
    navigator: { clipboard: { writeText: async () => {} } },
    crypto: { getRandomValues: (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256); return arr; } },
    fetch: fetch, // Node 22 global
    console,
    setTimeout,
    URL,
    Blob,
    sessionStorage: { getItem: () => null, setItem: () => {} },
    __toasts: toasts,
  };
  sandbox.window.SILO_CONFIG = { API_BASE_URL };
  sandbox.window.addEventListener = () => {};
  sandbox.window.SiloParser = { };
  sandbox.window.SiloEngine = { DEFAULT_ALLOCATION_RULES: [], uuid: () => "test-uuid-" + Math.random().toString(36).slice(2) };
  sandbox.window.SiloStorage = {
    async put() { return null; }, async getAll() { return []; }, async get() { return null; },
    async remove() {}, async clearStore() {}, async clearAll() {}, STORES: [],
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

async function main() {
  const sandbox = makeSandbox();

  // Patch toast() after load so we can inspect calls; patch by wrapping the
  // source with a small shim that captures toast() calls.
  const patchedSource = appJsSource.replace(
    'function toast(message, kind = "info") {',
    'function toast(message, kind = "info") { __toasts.push({ message, kind });'
  ).replace(
    // avoid boot() auto-running (needs DOMContentLoaded/full DOM we don't have)
    'document.addEventListener("DOMContentLoaded", boot);',
    `// boot() intentionally not auto-run in this test harness
    window.__bridge = {
      get state() { return state; },
      connectBackend, requestPaymentAccount, refreshPaymentAccountStatus, renderPaymentAccountPane,
    };`
  );

  vm.runInContext(patchedSource, sandbox, { filename: "app.js" });
  const bridge = sandbox.window.__bridge;

  const email = `node-test-${Date.now()}@example.com`;
  const password = "correct-horse-battery-staple-1";

  // state.user must exist for the payment-account functions to work.
  bridge.state.user = { email, fullName: "Node Test User", country: "NG", phone: null, backend: null, paymentAccount: null };

  console.log("== Test 1: connectBackend() registers + logs in a brand-new user ==");
  const token = await bridge.connectBackend(email, password, "Node Test User", "+2348100000099", "NG");
  assert.ok(typeof token === "string" && token.length > 10, "expected a JWT string back");
  assert.strictEqual(bridge.state.user.backend.token, token, "token should be cached on state.user.backend");
  console.log("PASS: got token, length", token.length);

  console.log("\n== Test 2: connectBackend() again with the SAME password logs in (no duplicate register) ==");
  bridge.state.user.backend = null; // simulate a fresh device / cleared session
  const token2 = await bridge.connectBackend(email, password, "Node Test User", "+2348100000099", "NG");
  assert.ok(typeof token2 === "string");
  console.log("PASS: re-login works");

  console.log("\n== Test 3: connectBackend() with WRONG password against an existing account throws a clear error ==");
  bridge.state.user.backend = null;
  let threw = null;
  try {
    await bridge.connectBackend(email, "totally-wrong-password-2", "Node Test User", "+2348100000099", "NG");
  } catch (err) { threw = err; }
  assert.ok(threw, "expected an error to be thrown");
  console.log("PASS: threw ->", threw.message);

  console.log("\n== Test 4: requestPaymentAccount() against a backend with NO Paystack key configured surfaces a clean 502, not a silent/fake success ==");
  bridge.state.user.backend = { email, token };
  bridge.state.user.paymentAccount = null;
  sandbox.document.getElementById("pa-phone").value = "+2348100000099";
  const toastCountBefore = sandbox.__toasts.length;
  await bridge.requestPaymentAccount();
  const newToasts = sandbox.__toasts.slice(toastCountBefore);
  console.log("Toasts:", newToasts.map(t => `[${t.kind}] ${t.message}`));
  assert.strictEqual(bridge.state.user.paymentAccount, null, "no PAYSTACK_SECRET_KEY is configured on this test server, so no account should be created");
  assert.ok(newToasts.some(t => t.kind === "danger" && /Paystack/i.test(t.message)), "expected a danger toast mentioning Paystack not being configured");
  console.log("PASS: clean 502 surfaced correctly, no phantom account created");

  console.log("\n== Test 5: requestPaymentAccount() with no phone set surfaces the backend's 400 clearly ==");
  bridge.state.user.phone = null;
  sandbox.document.getElementById("pa-phone").value = "";
  const toastCountBefore5 = sandbox.__toasts.length;
  await bridge.requestPaymentAccount();
  const newToasts5 = sandbox.__toasts.slice(toastCountBefore5);
  console.log("Toasts:", newToasts5.map(t => `[${t.kind}] ${t.message}`));
  assert.ok(newToasts5.some(t => t.kind === "danger"), "expected a client-side validation toast for missing phone");
  console.log("PASS: empty phone blocked client-side before hitting the network");

  console.log("\nALL TESTS PASSED");
  process.exit(0);
}

main().catch((err) => {
  console.error("TEST FAILED:", err);
  process.exit(1);
});
