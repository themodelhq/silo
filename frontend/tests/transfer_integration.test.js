// Loads the ACTUAL frontend/js source into a sandbox and drives the new
// "Account" transfer functions (loadBanksIfNeeded, handleAccountNumberInput,
// handleAccountTransfer) against a live backend instance. The test backend
// has no PAYSTACK_SECRET_KEY configured, so this verifies request plumbing,
// auth headers, and graceful error handling — not a live Paystack call.
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

function readJs(rel) { return fs.readFileSync(path.join(__dirname, "..", "js", rel), "utf8"); }
const API_BASE_URL = process.env.TEST_API_BASE_URL || "http://127.0.0.1:8123";

function makeSandbox() {
  const fakeStorage = { data: {} };
  const documentStub = {
    getElementById(id) {
      if (!fakeStorage.data[id]) {
        fakeStorage.data[id] = {
          value: "", textContent: "", innerHTML: "", style: {}, dataset: {}, disabled: false,
          addEventListener() {}, appendChild() {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
        };
      }
      return fakeStorage.data[id];
    },
    createElement() {
      const el = { textContent: "", style: {}, remove() {} };
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
    __els: fakeStorage.data,
  };
  const sandbox = {
    window: {}, document: documentStub,
    navigator: { clipboard: { writeText: async () => {} } },
    crypto: { getRandomValues: (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256); return arr; } },
    fetch, console, setTimeout, clearTimeout,
    sessionStorage: { getItem: () => null, setItem: () => {} },
  };
  sandbox.window.SILO_CONFIG = { API_BASE_URL };
  sandbox.window.addEventListener = () => {};
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
  vm.runInContext(readJs("parser.js"), sandbox, { filename: "parser.js" });
  vm.runInContext(readJs("envelope-engine.js"), sandbox, { filename: "envelope-engine.js" });

  const appJsSource = readJs("app.js").replace(
    'document.addEventListener("DOMContentLoaded", boot);',
    `window.__bridge = {
      get state() { return state; },
      connectBackend, loadBanksIfNeeded, handleAccountNumberInput, handleAccountTransfer, saveEnvelope,
    };`
  );
  vm.runInContext(appJsSource, sandbox, { filename: "app.js" });
  const bridge = sandbox.window.__bridge;
  const doc = sandbox.document;

  const email = `node-transfer-test-${Date.now()}@example.com`;
  const password = "correct-horse-battery-1";
  bridge.state.user = { email, fullName: "Transfer Test User", country: "NG", phone: "+2348100000099", backend: null, paymentAccount: null };

  console.log("== Setup: connect a backend session ==");
  await bridge.connectBackend(email, password, "Transfer Test User", "+2348100000099", "NG");
  assert.ok(bridge.state.user.backend?.token, "expected a cached backend token");
  console.log("PASS: connected");

  console.log("\n== Setup: seed a funded envelope ==");
  const envelope = { id: "env_1", name: "Spending", balance: 20000, allocated: 20000, color: "#6366F1", priority: 1, locked: false, archived: false, recurring: false };
  bridge.state.envelopes = { [envelope.id]: envelope };

  console.log("\n== Test 1: loadBanksIfNeeded() against a backend with no Paystack key fails gracefully ==");
  await bridge.loadBanksIfNeeded();
  const bankSelectHtml = doc.getElementById("transfer-bank").innerHTML;
  console.log("Bank select innerHTML:", bankSelectHtml);
  assert.ok(/Couldn't load banks/.test(bankSelectHtml), "expected a graceful error option, not a crash");
  console.log("PASS: no crash, clear error shown in the select itself");

  console.log("\n== Test 2: handleAccountNumberInput() fails gracefully without a configured provider ==");
  doc.getElementById("transfer-bank").value = "035";
  doc.getElementById("transfer-account-number").value = "0022728151";
  await bridge.handleAccountNumberInput();
  const resolvedText = doc.getElementById("transfer-account-resolved").textContent;
  console.log("Resolved text:", resolvedText);
  assert.ok(resolvedText && !resolvedText.startsWith("✓"), "should not claim a verified account name when the provider call failed");
  console.log("PASS: shows the real error instead of a fake checkmark");

  console.log("\n== Test 3: handleAccountTransfer() does not deduct the envelope balance on failure ==");
  doc.getElementById("transfer-bank").value = "035";
  doc.getElementById("transfer-account-number").value = "0022728151";
  doc.getElementById("transfer-account-resolved").dataset.accountName = "JOHN A DOE"; // simulate a prior successful resolve
  doc.getElementById("transfer-reason").value = "Test";
  doc.getElementById("transfer-amount").value = "5000";
  doc.getElementById("transfer-confirm").textContent = "Transfer";

  const balanceBefore = envelope.balance;
  await bridge.handleAccountTransfer(envelope.id);
  assert.strictEqual(envelope.balance, balanceBefore, "balance must be unchanged when the backend call fails (502, no Paystack key)");
  console.log("PASS: envelope balance untouched after a failed transfer attempt:", envelope.balance);

  console.log("\nALL TESTS PASSED");
  process.exit(0);
}

main().catch((err) => {
  console.error("TEST FAILED:", err);
  process.exit(1);
});
