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
    assert.strictEqual(names1.join("|"), ["Basic Salary", "Clothing", "DSTV", "Health", "Rent"].sort().join("|"), "envelope catalog should be REPLACED with the payslip's exact names (including Basic Salary itself), not merged with defaults");

    const ruleNames1 = bridge.state.rules.map((r) => r.envelopeName).sort();
    assert.strictEqual(ruleNames1.join("|"), names1.join("|"), "allocation rules should match the new envelope names exactly");

    // The core invariant: Available Balance (sum of every envelope) must
    // equal Net Salary EXACTLY — even though the line items themselves sum
    // to something else (447,000, including Basic Salary) than net salary
    // (147,000). This only holds if funding is proportionally scaled to
    // net salary rather than funded as the raw payslip-stated amounts.
    const rawTotal = 300000 + 100000 + 20000 + 15000 + 12000; // 447,000
    const totalBalance1 = Object.values(bridge.state.envelopes).reduce((s, e) => s + e.balance, 0);
    assert.strictEqual(totalBalance1, 147000, "Available Balance must equal Net Salary exactly");
    console.log(`PASS: Available Balance (${totalBalance1}) === Net Salary (147000)`);

    const rentRule = bridge.state.rules.find((r) => r.envelopeName === "Rent");
    const expectedPct = 100000 / rawTotal; // share of the FULL breakdown, not of net salary
    assert.ok(Math.abs(rentRule.value - expectedPct) < 0.0001, `Rent rule value ${rentRule.value} should equal ${expectedPct}`);
    console.log(`PASS: Rent allocation rule = ${(rentRule.value * 100).toFixed(2)}% of the payslip breakdown (447,000), not of net salary`);

    const rentEnv = Object.values(bridge.state.envelopes).find((e) => e.name === "Rent");
    const clothingEnv0 = Object.values(bridge.state.envelopes).find((e) => e.name === "Clothing");
    // Match the same rounding order as the real code: the percentage is
    // rounded to 4dp first, and *that* rounded value funds the envelope.
    const roundedRentPct = Math.round((100000 / rawTotal) * 10000) / 10000;
    const expectedRentBalance = Math.round(roundedRentPct * 147000 * 100) / 100;
    assert.strictEqual(rentEnv.balance, expectedRentBalance, "Rent should be funded as its proportional share of net salary, not the raw payslip figure");
    assert.ok(rentEnv.balance > clothingEnv0.balance, "Rent (100,000 on the payslip) should still get proportionally more than Clothing (20,000)");
    console.log("PASS: Rent envelope balance = ", rentEnv.balance, "(scaled down from the raw 100,000 on the payslip since it summed to more than net salary)");

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
        ["Basic Salary", "Clothing", "Rent", "Savings", "Transport"].sort().join("|"),
        "catalog should now match payslip 2's line items (Basic Salary included), plus the one locked survivor"
      );

      const ruleNames2 = bridge.state.rules.map((r) => r.envelopeName).sort();
      assert.strictEqual(ruleNames2.join("|"), ["Basic Salary", "Rent", "Savings", "Transport"].sort().join("|"), "rules should be fully replaced by payslip 2's line items (locked envelope keeps no stale rule)");

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
