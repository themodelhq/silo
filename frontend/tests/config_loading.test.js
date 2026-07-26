// Loads the ACTUAL frontend/js/config.js file (not a manually-constructed
// window.SILO_CONFIG stub) into a sandbox, exactly like a real browser
// script tag would. This exists because every other test in this suite
// manually set `sandbox.window.SILO_CONFIG = {...}` for convenience, which
// completely hid a real bug: config.js used `const SILO_CONFIG = {...}`,
// and a top-level const/let in a classic script does NOT become a `window`
// property (unlike `var`) — so `window.SILO_CONFIG` was `undefined` in
// every real browser, no matter what value was in the file or how caching
// was configured. This test loads config.js exactly the way index.html
// does and asserts window.SILO_CONFIG is actually populated afterward.
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

function main() {
  const configJsSource = fs.readFileSync(path.join(__dirname, "..", "js", "config.js"), "utf8");

  const sandbox = { window: {}, console };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  console.log("== Test: loading the real config.js populates window.SILO_CONFIG ==");
  vm.runInContext(configJsSource, sandbox, { filename: "config.js" });

  assert.ok(sandbox.window.SILO_CONFIG, "window.SILO_CONFIG must be defined after loading config.js — if this fails, config.js is using `const`/`let` instead of `window.SILO_CONFIG = ...`");
  assert.ok(
    typeof sandbox.window.SILO_CONFIG.API_BASE_URL === "string" && sandbox.window.SILO_CONFIG.API_BASE_URL.length > 0,
    "window.SILO_CONFIG.API_BASE_URL must be a non-empty string"
  );
  console.log("PASS: window.SILO_CONFIG =", sandbox.window.SILO_CONFIG);

  console.log("\n== Test: config.js does NOT rely on a bare top-level const/let for SILO_CONFIG ==");
  // Regression guard for the exact bug: fail loudly if anyone reverts this
  // back to `const SILO_CONFIG = ...` / `let SILO_CONFIG = ...`.
  assert.ok(
    !/^\s*(const|let)\s+SILO_CONFIG\s*=/m.test(configJsSource),
    "config.js must assign window.SILO_CONFIG directly, not declare a top-level const/let SILO_CONFIG"
  );
  console.log("PASS: config.js correctly assigns window.SILO_CONFIG directly");

  console.log("\nALL TESTS PASSED");
  process.exit(0);
}

main();
