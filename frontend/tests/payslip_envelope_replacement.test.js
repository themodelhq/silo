// Loads the ACTUAL frontend/js source (parser.js, envelope-engine.js,
// app.js) into a sandboxed environment and drives applyPayslipEnvelopeSplit()
// directly to verify: (1) envelope names/percentages come verbatim from the
// payslip's own line items, (2) they fully replace the seeded defaults, and
// (3) a second payslip with different line items replaces the first split
// rather than merging with it.
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

function readJs(rel) { return fs.readFileSync(path.join(__dirname, "..", "js", rel), "utf8"); }

function makeSandbox() {
  const fakeStorage = { data: {} };
  const documentStub = {
    getElementById(id) {
      if (!fakeStorage.data[id]) {
        fakeStorage.data[id] = {
          value: "", textContent: "", style: {}, innerHTML: "",
          addEventListener() {}, appendChild() {}, classList: { add() {}, remove() {}, toggle() {} },
        };
      }
      return fakeStorage.data[id];
    },
    createElement() { return { textContent: "", style: {}, remove() {} }; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const sandbox = {
    window: {},
    document: documentStub,
    navigator: { clipboard: { writeText: async () => {} } },
    crypto: { getRandomValues: (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256); return arr; } },
    fetch: async () => { throw new Error("network should not be used in this test"); },
    console,
    setTimeout,
    sessionStorage: { getItem: () => null, setItem: () => {} },
  };
  sandbox.window.SILO_CONFIG = { API_BASE_URL: "" };
  sandbox.window.addEventListener = () => {};
  sandbox.window.SiloStorage = {
    async put() { return null; }, async getAll() { return []; }, async get() { return null; },
    async remove() {}, async clearStore() {}, async clearAll() {}, STORES: [],
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

function main() {
  const sandbox = makeSandbox();

  vm.runInContext(readJs("parser.js"), sandbox, { filename: "parser.js" });
  vm.runInContext(readJs("envelope-engine.js"), sandbox, { filename: "envelope-engine.js" });

  const appJsSource = readJs("app.js").replace(
    'document.addEventListener("DOMContentLoaded", boot);',
    `window.__bridge = {
      get state() { return state; },
      applyPayslipEnvelopeSplit, seedDefaultEnvelopes, saveEnvelope, saveRules,
    };`
  );
  vm.runInContext(appJsSource, sandbox, { filename: "app.js" });
  const bridge = sandbox.window.__bridge;

  // Fresh user: seed the default catalog directly (mirrors what
  // seedDefaultEnvelopes() does, but deterministically for this test —
  // that function is intentionally fire-and-forget in the real app).
  bridge.state.user = { id: "u1", fullName: "Test User", email: "t@example.com" };
  bridge.state.rules = sandbox.window.SiloEngine.DEFAULT_ALLOCATION_RULES.map((r) => ({ ...r, id: "r_" + r.envelopeName }));
  bridge.state.envelopes = {};
  for (const r of bridge.state.rules) {
    const env = { id: "e_" + r.envelopeName, name: r.envelopeName, balance: 0, allocated: 0, color: r.color, priority: r.priority, locked: false, archived: false, recurring: false };
    bridge.state.envelopes[env.id] = env;
  }

  console.log("== Before any payslip: default envelopes ==");
  const defaultNames = Object.values(bridge.state.envelopes).map((e) => e.name).sort();
  console.log(defaultNames);
  assert.strictEqual(defaultNames.join("|"), ["Discretionary Spending", "Emergency Fund", "Rent", "Savings", "Transportation", "Utilities"].sort().join("|"));

  console.log("\n== Test 1: parsing a payslip with line items replaces the default catalog ==");
  const payslip1 = `
Employee Name: Jane Doe
Basic Salary: 300,000.00 NGN
PAYE Tax: 20,000.00
BUDGET SPLIT:
Rent: 100,000.00
Clothing: 20,000.00
Health: 15,000.00
DSTV: 12,000.00
NET TAKE HOME: 147,000.00
`;
  const parsed1 = sandbox.window.SiloParser.parsePayslip(payslip1);
  assert.ok(parsed1.lineItems.length > 0, "expected line items to be detected");
  console.log("Detected line items:", parsed1.lineItems);

  bridge.applyPayslipEnvelopeSplit(parsed1).then((count1) => {
    const names1 = Object.values(bridge.state.envelopes).map((e) => e.name).sort();
    console.log("Envelopes after payslip 1:", names1, "count:", count1);
    assert.strictEqual(names1.join("|"), ["Clothing", "DSTV", "Health", "Rent"].sort().join("|"), "envelope catalog should be REPLACED with the payslip's exact names, not merged with defaults");

    const ruleNames1 = bridge.state.rules.map((r) => r.envelopeName).sort();
    assert.strictEqual(ruleNames1.join("|"), names1.join("|"), "allocation rules should match the new envelope names exactly");

    const rentRule = bridge.state.rules.find((r) => r.envelopeName === "Rent");
    const expectedPct = 100000 / 147000;
    assert.ok(Math.abs(rentRule.value - expectedPct) < 0.0001, `Rent rule value ${rentRule.value} should equal ${expectedPct}`);
    console.log(`PASS: Rent allocation rule = ${(rentRule.value * 100).toFixed(2)}% (derived from the payslip, not a hardcoded default)`);

    const rentEnv = Object.values(bridge.state.envelopes).find((e) => e.name === "Rent");
    assert.strictEqual(rentEnv.balance, 100000);
    console.log("PASS: Rent envelope balance = ", rentEnv.balance);

    console.log("\n== Test 2: locking an envelope protects it from the next replacement ==");
    const clothingEnv = Object.values(bridge.state.envelopes).find((e) => e.name === "Clothing");
    clothingEnv.locked = true;

    const payslip2 = `
Employee Name: Jane Doe
Basic Salary: 300,000.00 NGN
BUDGET SPLIT:
Rent: 90,000.00
Savings: 30,000.00
Transport: 15,000.00
NET TAKE HOME: 135,000.00
`;
    const parsed2 = sandbox.window.SiloParser.parsePayslip(payslip2);
    console.log("Detected line items (2nd payslip):", parsed2.lineItems);

    return bridge.applyPayslipEnvelopeSplit(parsed2).then((count2) => {
      const names2 = Object.values(bridge.state.envelopes).map((e) => e.name).sort();
      console.log("Envelopes after payslip 2:", names2, "count:", count2);
      assert.ok(names2.includes("Clothing"), "the LOCKED envelope from payslip 1 should survive the replacement");
      assert.ok(!names2.includes("DSTV") && !names2.includes("Health"), "unlocked envelopes not on payslip 2 should be removed");
      assert.strictEqual(
        names2.sort().join("|"),
        ["Clothing", "Rent", "Savings", "Transport"].sort().join("|"),
        "catalog should now match payslip 2's line items, plus the one locked survivor"
      );

      const ruleNames2 = bridge.state.rules.map((r) => r.envelopeName).sort();
      assert.strictEqual(ruleNames2.join("|"), ["Rent", "Savings", "Transport"].sort().join("|"), "rules should be fully replaced by payslip 2's line items (locked envelope keeps no stale rule)");

      console.log("PASS: second payslip parse replaced the split, preserving only the locked envelope");
      console.log("\nALL TESTS PASSED");
      process.exit(0);
    });
  }).catch((err) => {
    console.error("TEST FAILED:", err);
    process.exit(1);
  });
}

main();
