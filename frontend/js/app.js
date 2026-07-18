/**
 * Silo App
 * =================
 * Client-side routing, state, and rendering. All budgeting logic comes from
 * js/parser.js and js/envelope-engine.js (both deterministic, both mirror
 * the Python backend exactly). This file is purely UI wiring + persistence
 * via js/storage.js (IndexedDB) so the whole app works offline.
 */

const PEParser = window.SiloParser;
const PEEngine = window.SiloEngine;
const genId = window.SiloEngine.uuid;
const Storage = window.SiloStorage;

const CURRENCY_SYMBOLS = { NGN: "₦", GHS: "GH₵", KES: "KSh", ZAR: "R" };
const COUNTRY_TO_CURRENCY = { NG: "NGN", GH: "GHS", KE: "KES", ZA: "ZAR" };

const state = {
  user: null,
  envelopes: {},        // id -> envelope
  rules: PEEngine.DEFAULT_ALLOCATION_RULES.map((r) => ({ ...r, id: genId() })),
  transactions: [],
  payslips: [],
  selectedCountry: "NG",
  pendingParse: null,
  settings: { currency: "NGN", country: "NG", theme: "dark", highContrast: false },
};

function fmt(amount, currency) {
  const symbol = CURRENCY_SYMBOLS[currency || state.settings.currency] || "₦";
  const n = Number(amount || 0);
  return `${symbol}${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function toast(message, kind = "info") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = "toast";
  const colors = { success: "var(--color-success)", danger: "var(--color-danger)", info: "var(--color-accent)" };
  el.innerHTML = `<span style="width:8px;height:8px;border-radius:50%;background:${colors[kind] || colors.info};flex-shrink:0;"></span>${message}`;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

/* ---------------------------------------------------------------------- */
/* Persistence                                                             */
/* ---------------------------------------------------------------------- */

async function loadState() {
  const profile = (await Storage.getAll("profile"))[0];
  if (profile) {
    state.user = profile;
    state.settings = { ...state.settings, ...(profile.settings || {}) };
  }

  const envs = await Storage.getAll("envelopes");
  state.envelopes = {};
  envs.forEach((e) => (state.envelopes[e.id] = e));

  const rules = await Storage.getAll("rules");
  if (rules.length) state.rules = rules;

  state.transactions = (await Storage.getAll("transactions")).sort((a, b) => new Date(b.occurredAt) - new Date(a.occurredAt));
  state.payslips = (await Storage.getAll("payslips")).sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

async function saveProfile() { if (state.user) await Storage.put("profile", state.user); }
async function saveEnvelope(env) { await Storage.put("envelopes", env); state.envelopes[env.id] = env; }
async function deleteEnvelopeStore(id) { await Storage.remove("envelopes", id); delete state.envelopes[id]; }
async function saveRules() { await Storage.clearStore("rules"); for (const r of state.rules) await Storage.put("rules", r); }
async function saveTransaction(t) { await Storage.put("transactions", t); state.transactions.unshift(t); }
async function savePayslip(p) { await Storage.put("payslips", p); state.payslips.unshift(p); }

/* ---------------------------------------------------------------------- */
/* Routing                                                                 */
/* ---------------------------------------------------------------------- */

function showView(id) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  const target = document.getElementById(`view-${id}`);
  if (target) target.classList.add("active");
}

function navigateApp(name) {
  showView(name);
  document.querySelectorAll("[data-nav]").forEach((el) => el.classList.toggle("active", el.dataset.nav === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "dashboard") renderDashboard();
  if (name === "envelopes") renderEnvelopes();
  if (name === "transactions") renderTransactions();
  if (name === "reports") renderReports();
  if (name === "history") renderHistory();
}

function enterApp() {
  document.getElementById("view-landing").classList.remove("active");
  document.getElementById("view-auth").classList.remove("active");
  document.getElementById("app-shell").style.display = "flex";
  document.getElementById("dash-username").textContent = state.user?.fullName ? `, ${state.user.fullName.split(" ")[0]}` : "";
  document.getElementById("dash-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  navigateApp("dashboard");
  maybeShowInstallBanner();
}

function exitApp() {
  document.getElementById("app-shell").style.display = "none";
  document.getElementById("view-auth").classList.remove("active");
  document.getElementById("view-landing").classList.add("active");
}

/* ---------------------------------------------------------------------- */
/* Dashboard                                                               */
/* ---------------------------------------------------------------------- */

function computeTotals() {
  const envs = Object.values(state.envelopes).filter((e) => !e.archived);
  const totalBalance = envs.reduce((s, e) => s + e.balance, 0);
  const totalAllocated = envs.reduce((s, e) => s + e.allocated, 0);
  const latestPayslip = state.payslips[0];
  const monthlyIncome = latestPayslip ? latestPayslip.netSalary : 0;
  const monthlyExpenses = state.transactions
    .filter((t) => t.type === "expense")
    .reduce((s, t) => s + t.amount, 0);
  return { totalBalance, totalAllocated, monthlyIncome, monthlyExpenses };
}

function renderDashboard() {
  const { totalBalance, monthlyIncome, monthlyExpenses } = computeTotals();
  const latestPayslip = state.payslips[0];

  const stats = [
    { label: "Net salary (latest)", value: fmt(monthlyIncome), delta: latestPayslip ? `${latestPayslip.employerName || "Employer"}` : "No payslip yet" },
    { label: "Available balance", value: fmt(totalBalance), delta: "Across all envelopes" },
    { label: "Monthly expenses", value: fmt(monthlyExpenses), delta: `${state.transactions.filter((t) => t.type === "expense").length} transactions` },
    { label: "Envelopes", value: String(Object.values(state.envelopes).filter((e) => !e.archived).length), delta: "Active buckets" },
  ];

  document.getElementById("dash-stats").innerHTML = stats.map((s) => `
    <div class="glass-card stat-tile">
      <div class="stat-label">${s.label}</div>
      <div class="stat-value">${s.value}</div>
      <div class="stat-delta">${s.delta}</div>
    </div>`).join("");

  const envs = Object.values(state.envelopes).filter((e) => !e.archived).slice(0, 6);
  document.getElementById("dash-envelopes").innerHTML = envs.length
    ? envs.map(envelopeCardHTML).join("")
    : `<div class="empty-state" style="grid-column:1/-1;">No envelopes yet. Upload a payslip to auto-create them.</div>`;

  document.getElementById("dash-bills").innerHTML = `
    <div class="txn-row"><div class="txn-left"><div class="txn-icon">🏠</div><div class="txn-info"><div class="txn-title">Rent</div><div class="txn-sub">Due on the 1st</div></div></div><span class="badge badge-warning">Upcoming</span></div>
    <div class="txn-row"><div class="txn-left"><div class="txn-icon">💡</div><div class="txn-info"><div class="txn-title">Electricity (PHCN/EKEDC)</div><div class="txn-sub">Due in 5 days</div></div></div><span class="badge badge-neutral">Scheduled</span></div>
    <div class="txn-row"><div class="txn-left"><div class="txn-icon">📶</div><div class="txn-info"><div class="txn-title">DSTV subscription</div><div class="txn-sub">Due in 12 days</div></div></div><span class="badge badge-neutral">Scheduled</span></div>`;

  const recent = state.transactions.slice(0, 6);
  document.getElementById("dash-transactions").innerHTML = recent.length
    ? recent.map(transactionRowHTML).join("")
    : `<div class="empty-state">No transactions yet.</div>`;
  attachEnvelopeCardListeners();
}

/* ---------------------------------------------------------------------- */
/* Envelopes                                                               */
/* ---------------------------------------------------------------------- */

function envelopeCardHTML(env) {
  const pct = env.allocated > 0 ? Math.min(100, Math.max(0, ((env.allocated - env.balance) / env.allocated) * 100)) : 0;
  const overspent = env.balance < 0;
  return `
    <div class="envelope-card" style="--envelope-color:${env.color}" data-envelope-id="${env.id}" tabindex="0">
      <div class="env-badges">
        ${env.locked ? '<div class="env-badge" title="Locked"><svg viewBox="0 0 24 24" fill="none"><rect x="5" y="11" width="14" height="9" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M8 11V7a4 4 0 018 0v4" stroke="currentColor" stroke-width="1.6"/></svg></div>' : ""}
        ${env.recurring ? '<div class="env-badge" title="Recurring"><svg viewBox="0 0 24 24" fill="none"><path d="M4 4v5h5M20 20v-5h-5M4.5 9a8 8 0 0113.3-3.5L20 8M19.5 15a8 8 0 01-13.3 3.5L4 16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></div>' : ""}
      </div>
      <span class="env-icon-dot"></span>
      <div class="env-name">${escapeHTML(env.name)}</div>
      <div class="env-balance" style="${overspent ? "color:var(--color-danger)" : ""}">${fmt(env.balance)}</div>
      <div class="env-meta">of ${fmt(env.allocated)} allocated</div>
      <div class="env-progress-track"><div class="env-progress-fill" style="width:${pct}%;"></div></div>
    </div>`;
}

function renderEnvelopes() {
  const envs = Object.values(state.envelopes).filter((e) => !e.archived).sort((a, b) => a.priority - b.priority);
  document.getElementById("envelopes-grid").innerHTML = envs.length
    ? envs.map(envelopeCardHTML).join("")
    : `<div class="empty-state" style="grid-column:1/-1;">
        <svg viewBox="0 0 24 24" fill="none"><path d="M3 6l9 6 9-6M4 5h16a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
        <div>No envelopes yet. Create one, or upload a payslip and confirm allocation.</div>
      </div>`;
  attachEnvelopeCardListeners();
}

function attachEnvelopeCardListeners() {
  document.querySelectorAll("[data-envelope-id]").forEach((card) => {
    card.addEventListener("click", () => openEnvelopeDetail(card.dataset.envelopeId));
    card.addEventListener("keypress", (e) => { if (e.key === "Enter") openEnvelopeDetail(card.dataset.envelopeId); });
  });
}

function openEnvelopeDetail(id) {
  const env = state.envelopes[id];
  if (!env) return;
  document.getElementById("env-detail-name").textContent = env.name;
  const otherEnvs = Object.values(state.envelopes).filter((e) => e.id !== id && !e.archived);
  document.getElementById("env-detail-body").innerHTML = `
    <div class="glass-card" style="--envelope-color:${env.color};padding:16px;margin-bottom:16px;">
      <div class="env-balance">${fmt(env.balance)}</div>
      <div class="env-meta">of ${fmt(env.allocated)} allocated · priority ${env.priority}</div>
    </div>
    <div class="grid grid-2" style="gap:8px;">
      <button class="btn btn-secondary btn-sm" data-detail-action="rename">Rename</button>
      <button class="btn btn-secondary btn-sm" data-detail-action="${env.locked ? "unlock" : "lock"}">${env.locked ? "Unlock" : "Lock"}</button>
      <button class="btn btn-secondary btn-sm" data-detail-action="archive">Archive</button>
      <button class="btn btn-danger btn-sm" data-detail-action="delete">Delete</button>
    </div>
    ${otherEnvs.length ? `
    <div class="field mt-16">
      <label>Transfer to</label>
      <div class="flex gap-8">
        <select class="input" id="transfer-target">${otherEnvs.map((e) => `<option value="${e.id}">${escapeHTML(e.name)}</option>`).join("")}</select>
        <input class="input" id="transfer-amount" type="number" placeholder="Amount" style="max-width:120px;">
      </div>
      <button class="btn btn-primary btn-sm mt-8" id="transfer-confirm">Transfer</button>
    </div>` : ""}
  `;
  document.querySelectorAll("[data-detail-action]").forEach((btn) => {
    btn.addEventListener("click", () => handleEnvelopeDetailAction(id, btn.dataset.detailAction));
  });
  const transferBtn = document.getElementById("transfer-confirm");
  if (transferBtn) transferBtn.addEventListener("click", () => handleTransfer(id));
  openModal("envelope-detail");
}

async function handleEnvelopeDetailAction(id, action) {
  const env = state.envelopes[id];
  if (!env) return;
  if (action === "lock") { env.locked = true; await saveEnvelope(env); toast(`${env.name} locked.`, "success"); }
  else if (action === "unlock") { env.locked = false; await saveEnvelope(env); toast(`${env.name} unlocked.`, "success"); }
  else if (action === "archive") { env.archived = true; await saveEnvelope(env); toast(`${env.name} archived.`, "success"); closeModal("envelope-detail"); }
  else if (action === "delete") {
    if (env.locked) { toast("Unlock this envelope before deleting it.", "danger"); return; }
    await deleteEnvelopeStore(id);
    toast(`${env.name} deleted.`, "success");
    closeModal("envelope-detail");
  } else if (action === "rename") {
    const newName = prompt("New envelope name:", env.name);
    if (newName && newName.trim()) { env.name = newName.trim(); await saveEnvelope(env); toast("Envelope renamed.", "success"); }
  }
  renderEnvelopes(); renderDashboard();
  if (document.getElementById("modal-envelope-detail").classList.contains("active") && action !== "delete" && action !== "archive") {
    openEnvelopeDetail(id);
  }
}

async function handleTransfer(fromId) {
  const toId = document.getElementById("transfer-target").value;
  const amount = parseFloat(document.getElementById("transfer-amount").value);
  const from = state.envelopes[fromId];
  const to = state.envelopes[toId];
  if (!amount || amount <= 0) { toast("Enter a valid amount.", "danger"); return; }
  if (from.locked) { toast(`${from.name} is locked.`, "danger"); return; }
  if (from.balance < amount) { toast("Insufficient balance in source envelope.", "danger"); return; }
  from.balance = Math.round((from.balance - amount) * 100) / 100;
  to.balance = Math.round((to.balance + amount) * 100) / 100;
  await saveEnvelope(from); await saveEnvelope(to);
  await saveTransaction({ id: genId(), envelopeId: fromId, type: "transfer", amount, category: "Transfer", merchant: `To ${to.name}`, note: "", occurredAt: new Date().toISOString() });
  toast(`Transferred ${fmt(amount)} to ${to.name}.`, "success");
  closeModal("envelope-detail");
  renderEnvelopes(); renderDashboard();
}

/* ---------------------------------------------------------------------- */
/* Transactions                                                            */
/* ---------------------------------------------------------------------- */

const TXN_ICONS = { expense: "💸", income: "💰", transfer: "🔁", refund: "↩️" };

function transactionRowHTML(t) {
  const env = state.envelopes[t.envelopeId];
  const negative = t.type === "expense" || t.type === "transfer";
  return `
    <div class="txn-row">
      <div class="txn-left">
        <div class="txn-icon">${TXN_ICONS[t.type] || "💳"}</div>
        <div class="txn-info">
          <div class="txn-title">${escapeHTML(t.merchant || t.category || "Transaction")}</div>
          <div class="txn-sub">${env ? escapeHTML(env.name) : "Unassigned"} · ${new Date(t.occurredAt).toLocaleDateString()}</div>
        </div>
      </div>
      <span class="txn-amount ${negative ? "negative" : "positive"}">${negative ? "-" : "+"}${fmt(t.amount)}</span>
    </div>`;
}

function renderTransactions() {
  const search = (document.getElementById("txn-search").value || "").toLowerCase();
  const typeFilter = document.getElementById("txn-filter-type").value;
  let list = state.transactions;
  if (typeFilter) list = list.filter((t) => t.type === typeFilter);
  if (search) list = list.filter((t) => (t.merchant || "").toLowerCase().includes(search) || (t.note || "").toLowerCase().includes(search));
  document.getElementById("transactions-list").innerHTML = list.length
    ? list.map(transactionRowHTML).join("")
    : `<div class="empty-state">No transactions match.</div>`;
}

async function handleAddTransaction() {
  const type = document.getElementById("txn-type").value;
  const envelopeId = document.getElementById("txn-envelope").value;
  const amount = parseFloat(document.getElementById("txn-amount").value);
  const merchant = document.getElementById("txn-merchant").value.trim();
  const note = document.getElementById("txn-note").value.trim();

  if (!amount || amount <= 0) { toast("Enter a valid amount.", "danger"); return; }
  const env = state.envelopes[envelopeId];
  if (env) {
    if (env.locked) { toast(`${env.name} is locked.`, "danger"); return; }
    if (type === "expense") env.balance = Math.round((env.balance - amount) * 100) / 100;
    else env.balance = Math.round((env.balance + amount) * 100) / 100;
    await saveEnvelope(env);
  }
  await saveTransaction({ id: genId(), envelopeId: envelopeId || null, type, amount, category: env?.name || "Uncategorized", merchant, note, occurredAt: new Date().toISOString() });
  toast("Transaction saved.", "success");
  closeModal("add-transaction");
  document.getElementById("txn-amount").value = "";
  document.getElementById("txn-merchant").value = "";
  document.getElementById("txn-note").value = "";
  renderTransactions(); renderDashboard(); renderEnvelopes();
}

/* ---------------------------------------------------------------------- */
/* Payslip upload / parsing                                                */
/* ---------------------------------------------------------------------- */

function statusBadgeHTML(status) {
  const map = {
    OK: '<span class="badge badge-success">✓ Verified</span>',
    CALCULATED: '<span class="badge badge-info">Σ Calculated</span>',
    REVIEW_REQUIRED: '<span class="badge badge-warning">⚠ Review required</span>',
    INCOMPLETE: '<span class="badge badge-danger">Incomplete</span>',
  };
  return map[status] || "";
}

function runParse() {
  const text = document.getElementById("paste-area").value.trim();
  if (!text || text.length < 10) { toast("Paste or upload payslip text first.", "danger"); return; }
  const countryOverride = state.selectedCountry;
  const parsed = PEParser.parsePayslip(text, countryOverride);
  state.pendingParse = parsed;
  renderParseResult(parsed);
}

function renderParseResult(p) {
  document.getElementById("parse-empty-card").style.display = "none";
  const card = document.getElementById("parse-result-card");
  card.style.display = "block";
  document.getElementById("parse-status-badge").innerHTML = statusBadgeHTML(p.validationStatus);

  document.getElementById("parse-notes").innerHTML = p.extractionNotes.length
    ? `<div class="glass-card" style="padding:12px;background:rgba(245,158,11,0.06);border-color:rgba(245,158,11,0.2);font-size:12.5px;">${p.extractionNotes.map(escapeHTML).join("<br>")}</div>`
    : "";

  const rows = [
    ["Employee", p.employeeName || "—"],
    ["Employer", p.employer || "—"],
    ["Basic salary", fmt(p.basicSalary, p.currency)],
    ["Housing allowance", fmt(p.housingAllowance, p.currency)],
    ["Transport allowance", fmt(p.transportAllowance, p.currency)],
    ["Utility allowance", fmt(p.utilityAllowance, p.currency)],
    ["Bonus", fmt(p.bonus, p.currency)],
    ["Tax (PAYE)", fmt(p.tax, p.currency)],
    ["Pension", fmt(p.pension, p.currency)],
    ["NHF / statutory", fmt(p.nhf, p.currency)],
    ["Gross salary", fmt(p.grossSalary, p.currency)],
    ["Net salary", fmt(p.netSalary, p.currency)],
  ];
  document.getElementById("parse-fields").innerHTML = rows.map(([label, value]) => `
    <div class="flex justify-between" style="padding:7px 0;border-bottom:1px solid var(--surface-border);font-size:13px;">
      <span class="text-secondary">${label}</span><span style="font-weight:600;">${value}</span>
    </div>`).join("");
}

// Deterministic color palette cycled across auto-generated envelopes so the
// same payslip always produces the same colors run to run — mirrors
// backend/app/routers/payslips.py's _ENVELOPE_COLOR_PALETTE.
const ENVELOPE_COLOR_PALETTE = [
  "#6366F1", "#38BDF8", "#10B981", "#F59E0B", "#EF4444",
  "#8B5CF6", "#EC4899", "#14B8A6", "#F97316", "#84CC16",
];

/**
 * Turns a payslip's line items into the user's new allocation rule set and
 * envelope catalog — using the *exact names* found on the payslip (Rent,
 * Clothing, Health, ...), each weighted as a percentage of net salary.
 * This fully replaces the previous rule set (the seeded defaults, or an
 * earlier payslip's split) and deletes any envelope that isn't part of the
 * new split, except locked ones — locking is an explicit protection the
 * person opted into elsewhere in the app, so a payslip re-parse doesn't
 * override it. Returns the number of envelopes now in the catalog.
 */
async function applyPayslipEnvelopeSplit(p) {
  const base = p.netSalary > 0 ? p.netSalary : p.lineItems.reduce((sum, li) => sum + li.amount, 0);
  if (base <= 0) return Object.keys(state.envelopes).length;

  const newRules = p.lineItems
    .filter((li) => li.amount > 0)
    .map((li, i) => ({
      id: genId(),
      envelopeName: li.name,
      type: PEEngine.AllocationType.PERCENTAGE,
      value: Math.min(li.amount / base, 1),
      color: ENVELOPE_COLOR_PALETTE[i % ENVELOPE_COLOR_PALETTE.length],
      priority: i + 1,
    }));

  const newNames = new Set(newRules.map((r) => r.envelopeName));

  // Replace the active rule set — this becomes the new default split until
  // a different payslip (with different line items) is parsed.
  state.rules = newRules;
  await saveRules();

  // Replace the envelope catalog to match, preserving locked envelopes even
  // if they're not part of the new split.
  const skippedLocked = [];
  for (const env of Object.values(state.envelopes)) {
    if (newNames.has(env.name)) continue;
    if (env.locked) { skippedLocked.push(env.name); continue; }
    await deleteEnvelopeStore(env.id);
  }
  if (skippedLocked.length) {
    toast(`Kept ${skippedLocked.length} locked envelope(s) not on this payslip: ${skippedLocked.join(", ")}.`, "info");
  }

  for (const rule of newRules) {
    const amount = Math.round(rule.value * base * 100) / 100;
    const existing = Object.values(state.envelopes).find((e) => e.name === rule.envelopeName);
    const record = existing
      ? { ...existing, balance: amount, allocated: amount, color: rule.color, priority: rule.priority }
      : { id: genId(), name: rule.envelopeName, balance: amount, allocated: amount, color: rule.color, priority: rule.priority, locked: false, archived: false, recurring: false };
    await saveEnvelope(record);
  }

  return Object.keys(state.envelopes).length;
}

async function confirmAllocatePayslip() {
  const p = state.pendingParse;
  if (!p) return;

  const payslipRecord = {
    id: genId(), employerName: p.employer, payrollMonth: p.payrollMonth, payrollYear: p.payrollYear,
    basicSalary: p.basicSalary, netSalary: p.netSalary, grossSalary: p.grossSalary,
    tax: p.tax, pension: p.pension, nhf: p.nhf, currency: p.currency, country: p.country,
    validationStatus: p.validationStatus, extractionNotes: p.extractionNotes, createdAt: new Date().toISOString(),
  };
  await savePayslip(payslipRecord);

  let envelopeCount;
  if (p.lineItems && p.lineItems.length) {
    // The payslip itself listed named budget categories (e.g. Rent,
    // Clothing, Health) — those exact names and their share of net salary
    // become this user's envelope catalog and allocation split, replacing
    // whatever was active before (the seeded defaults, or a prior payslip's
    // split). This stays the default until a different payslip is parsed.
    envelopeCount = await applyPayslipEnvelopeSplit(p);
  } else {
    // No named line items on this payslip — fall back to allocating net
    // salary across whatever rule set is currently active.
    const engine = new PEEngine.EnvelopeEngine(state.rules);
    Object.values(state.envelopes).forEach((e) => { engine.envelopes[e.name] = { ...e }; });
    engine.allocate(p.netSalary);
    for (const [name, env] of Object.entries(engine.envelopes)) {
      const existing = Object.values(state.envelopes).find((e) => e.name === name);
      const record = existing ? { ...existing, balance: env.balance, allocated: env.allocated } : { ...env };
      await saveEnvelope(record);
    }
    envelopeCount = Object.keys(engine.envelopes).length;
  }

  state.settings.currency = p.currency;
  state.settings.country = p.country;
  if (state.user) { state.user.settings = state.settings; await saveProfile(); }

  toast(`${fmt(p.netSalary, p.currency)} allocated across ${envelopeCount} envelopes.`, "success");
  document.getElementById("paste-area").value = "";
  document.getElementById("parse-result-card").style.display = "none";
  document.getElementById("parse-empty-card").style.display = "block";
  state.pendingParse = null;
  navigateApp("dashboard");
}

/* File reading: for .txt we read text directly; for PDFs/images we can't run
   OCR client-side without a heavy dependency, so we prompt the user to paste
   the text instead — this keeps the parser 100% deterministic rather than
   silently guessing at binary content. */
function handleFileSelected(file) {
  if (!file) return;
  if (file.type === "text/plain" || file.name.endsWith(".txt")) {
    const reader = new FileReader();
    reader.onload = () => { document.getElementById("paste-area").value = reader.result; toast("File loaded. Review and parse.", "success"); };
    reader.readAsText(file);
  } else {
    toast("For PDFs/images, please paste the payslip text below — Silo parses text deterministically without cloud OCR.", "info");
  }
}

/* ---------------------------------------------------------------------- */
/* History                                                                  */
/* ---------------------------------------------------------------------- */

function renderHistory() {
  const list = state.payslips;
  document.getElementById("history-list").innerHTML = list.length
    ? list.map((p) => `
      <div class="txn-row">
        <div class="txn-left">
          <div class="txn-icon">📄</div>
          <div class="txn-info">
            <div class="txn-title">${escapeHTML(p.employerName || "Payslip")} ${p.payrollMonth ? `— ${monthName(p.payrollMonth)} ${p.payrollYear || ""}` : ""}</div>
            <div class="txn-sub">${new Date(p.createdAt).toLocaleDateString()} · Net ${fmt(p.netSalary, p.currency)}</div>
          </div>
        </div>
        ${statusBadgeHTML(p.validationStatus)}
      </div>`).join("")
    : `<div class="empty-state">No payslips parsed yet.</div>`;
}

function monthName(n) {
  return ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][n] || "";
}

/* ---------------------------------------------------------------------- */
/* Reports (client-side equivalent of backend/app/routers/reports.py)      */
/* ---------------------------------------------------------------------- */

function renderReports() {
  const period = document.getElementById("report-period").value;
  const daysLookup = { daily: 1, weekly: 7, monthly: 30, quarterly: 90, annual: 365 };
  const since = new Date(Date.now() - daysLookup[period] * 86400000);
  const inRange = state.transactions.filter((t) => new Date(t.occurredAt) >= since);

  const income = inRange.filter((t) => t.type === "income" || t.type === "refund").reduce((s, t) => s + t.amount, 0);
  const expenses = inRange.filter((t) => t.type === "expense").reduce((s, t) => s + t.amount, 0);

  document.getElementById("report-stats").innerHTML = [
    { label: "Total income", value: fmt(income) },
    { label: "Total expenses", value: fmt(expenses) },
    { label: "Net cash flow", value: fmt(income - expenses) },
  ].map((s) => `<div class="glass-card stat-tile"><div class="stat-label">${s.label}</div><div class="stat-value">${s.value}</div></div>`).join("");

  const categoryTotals = {};
  inRange.filter((t) => t.type === "expense").forEach((t) => {
    const cat = t.category || "Uncategorized";
    categoryTotals[cat] = (categoryTotals[cat] || 0) + t.amount;
  });
  const sortedCats = Object.entries(categoryTotals).sort((a, b) => b[1] - a[1]);
  const maxCat = sortedCats.length ? sortedCats[0][1] : 1;
  document.getElementById("report-categories").innerHTML = sortedCats.length
    ? sortedCats.map(([cat, total]) => `
      <div style="margin-bottom:12px;">
        <div class="flex justify-between" style="font-size:13px;margin-bottom:4px;"><span>${escapeHTML(cat)}</span><span style="font-weight:600;">${fmt(total)}</span></div>
        <div class="progress-track"><div class="progress-fill" style="width:${(total / maxCat) * 100}%;"></div></div>
      </div>`).join("")
    : `<div class="empty-state">No expenses in this period.</div>`;

  const envs = Object.values(state.envelopes).filter((e) => !e.archived);
  document.getElementById("report-budget").innerHTML = envs.length
    ? envs.map((e) => {
        const spent = e.allocated - e.balance;
        const pctUsed = e.allocated > 0 ? Math.round((spent / e.allocated) * 100) : 0;
        const flag = e.balance < 0 ? '<span class="badge badge-danger">Over budget</span>' : pctUsed >= 90 ? '<span class="badge badge-warning">Near limit</span>' : '<span class="badge badge-success">On track</span>';
        return `<div style="margin-bottom:12px;">
          <div class="flex justify-between" style="font-size:13px;margin-bottom:4px;"><span>${escapeHTML(e.name)}</span>${flag}</div>
          <div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, pctUsed)}%;background:${e.balance < 0 ? "var(--color-danger)" : e.color};"></div></div>
        </div>`;
      }).join("")
    : `<div class="empty-state">No envelopes yet.</div>`;
}

function exportReportCSV() {
  const rows = [["Date", "Type", "Category", "Merchant", "Amount", "Note"]];
  state.transactions.forEach((t) => rows.push([new Date(t.occurredAt).toISOString(), t.type, t.category || "", t.merchant || "", t.amount, t.note || ""]));
  const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "silo-transactions.csv"; a.click();
  URL.revokeObjectURL(url);
  toast("CSV exported.", "success");
}

/* ---------------------------------------------------------------------- */
/* Allocation rules modal                                                  */
/* ---------------------------------------------------------------------- */

function renderRulesModal() {
  document.getElementById("rules-list").innerHTML = state.rules
    .sort((a, b) => a.priority - b.priority)
    .map((r) => `
      <div class="flex gap-8 items-center" style="margin-bottom:10px;">
        <span class="env-icon-dot" style="--envelope-color:${r.color};background:${r.color};"></span>
        <input class="input rule-name" data-rule-id="${r.id}" value="${escapeHTML(r.envelopeName)}" style="flex:1;">
        <select class="input rule-type" data-rule-id="${r.id}" style="max-width:110px;">
          <option value="PERCENTAGE" ${r.type === "PERCENTAGE" ? "selected" : ""}>%</option>
          <option value="FIXED" ${r.type === "FIXED" ? "selected" : ""}>Fixed</option>
          <option value="REMAINDER" ${r.type === "REMAINDER" ? "selected" : ""}>Remainder</option>
        </select>
        <input class="input rule-value" data-rule-id="${r.id}" type="number" value="${r.type === "PERCENTAGE" ? r.value * 100 : r.value}" style="max-width:90px;" ${r.type === "REMAINDER" ? "disabled" : ""}>
        <svg class="modal-close" data-remove-rule="${r.id}" viewBox="0 0 24 24" fill="none" style="flex-shrink:0;"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      </div>`).join("") + `<button class="btn btn-ghost btn-sm mt-8" id="add-rule-btn">+ Add rule</button>
      <button class="btn btn-primary btn-block mt-16" id="save-rules-btn">Save rules</button>`;

  document.querySelectorAll("[data-remove-rule]").forEach((el) => el.addEventListener("click", () => {
    state.rules = state.rules.filter((r) => r.id !== el.dataset.removeRule);
    renderRulesModal();
  }));
  document.getElementById("add-rule-btn").addEventListener("click", () => {
    state.rules.push({ id: genId(), envelopeName: "New envelope", type: "PERCENTAGE", value: 0.05, color: "#6366F1", priority: state.rules.length + 1 });
    renderRulesModal();
  });
  document.getElementById("save-rules-btn").addEventListener("click", async () => {
    document.querySelectorAll(".rule-name").forEach((input) => {
      const rule = state.rules.find((r) => r.id === input.dataset.ruleId);
      if (rule) rule.envelopeName = input.value;
    });
    document.querySelectorAll(".rule-type").forEach((select) => {
      const rule = state.rules.find((r) => r.id === select.dataset.ruleId);
      if (rule) rule.type = select.value;
    });
    document.querySelectorAll(".rule-value").forEach((input) => {
      const rule = state.rules.find((r) => r.id === input.dataset.ruleId);
      if (rule && rule.type !== "REMAINDER") rule.value = rule.type === "PERCENTAGE" ? parseFloat(input.value) / 100 : parseFloat(input.value);
    });
    try {
      window.SiloEngine.validateRuleSet(state.rules);
    } catch (e) { toast(e.message, "danger"); return; }
    await saveRules();
    toast("Allocation rules saved.", "success");
    closeModal("rules");
  });
}

/* ---------------------------------------------------------------------- */
/* Modals                                                                   */
/* ---------------------------------------------------------------------- */

function openModal(name) {
  document.getElementById(`modal-${name}`).classList.add("active");
  if (name === "add-transaction") {
    const select = document.getElementById("txn-envelope");
    select.innerHTML = `<option value="">Unassigned</option>` + Object.values(state.envelopes).filter((e) => !e.archived).map((e) => `<option value="${e.id}">${escapeHTML(e.name)}</option>`).join("");
  }
  if (name === "rules") renderRulesModal();
}
function closeModal(name) { document.getElementById(`modal-${name}`).classList.remove("active"); }
function closeAllModals() { document.querySelectorAll(".modal-overlay").forEach((m) => m.classList.remove("active")); }

/* ---------------------------------------------------------------------- */
/* Auth (local, device-only — see README for the real backend's JWT auth)  */
/* ---------------------------------------------------------------------- */

async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  if (!state.user || state.user.email !== email) {
    toast("No local account with that email on this device. Try creating an account.", "danger");
    return;
  }
  enterApp();
  // Best-effort: if this password also works against the Silo backend,
  // connect the Payment Account feature automatically. Never blocks local
  // login, and failures here are expected/normal (e.g. offline, or this
  // device was never connected to the backend) — see js/config.js.
  if (password) tryBootstrapBackendSession(email, password, state.user.fullName, state.user.phone, state.user.country);
}

async function handleRegister(e) {
  e.preventDefault();
  const fullName = document.getElementById("reg-name").value.trim();
  const email = document.getElementById("reg-email").value.trim();
  const country = document.getElementById("reg-country").value;
  const password = document.getElementById("reg-password").value;
  state.user = {
    id: genId(), fullName, email, country, phone: null, backend: null, paymentAccount: null,
    settings: { currency: COUNTRY_TO_CURRENCY[country], country, theme: "dark", highContrast: false },
  };
  state.settings = state.user.settings;
  await saveProfile();
  seedDefaultEnvelopes();
  toast("Account created.", "success");
  enterApp();
  // Same best-effort backend bootstrap as login — see js/config.js and the
  // "Payment account" section below for what this actually connects to.
  if (password) tryBootstrapBackendSession(email, password, fullName, null, country);
}

function seedDefaultEnvelopes() {
  // Give a first-time user a friendly starting point (zero balances until a payslip is confirmed).
  state.rules.forEach(async (r) => {
    if (!Object.values(state.envelopes).find((e) => e.name === r.envelopeName)) {
      const env = { id: genId(), name: r.envelopeName, balance: 0, allocated: 0, color: r.color, priority: r.priority, locked: false, archived: false, recurring: false };
      await saveEnvelope(env);
    }
  });
}

/* ---------------------------------------------------------------------- */
/* Payment account (Paystack, via the real Silo backend)                    */
/* ---------------------------------------------------------------------- */
/* This is the one feature in this build that talks to a server — every
 * other screen is 100% local (IndexedDB), per js/storage.js. The backend's
 * /payments endpoints require a JWT, so each device logs into (or, the
 * first time, registers) a bridge account on the backend using the same
 * email/password the person already enters on this device's local
 * login/register form (see handleLogin/handleRegister above) — the password
 * itself is never stored, only the resulting token, so re-authenticating
 * (e.g. after the 7-day token expires) means entering it again here.
 */

function apiBaseUrl() {
  const base = (window.SILO_CONFIG && window.SILO_CONFIG.API_BASE_URL) || "";
  return base.replace(/\/+$/, "");
}

async function backendFetch(path, options = {}) {
  const base = apiBaseUrl();
  if (!base) throw new Error("The backend isn't configured yet (SILO_CONFIG.API_BASE_URL in js/config.js).");

  let res;
  try {
    res = await fetch(base + path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  } catch (err) {
    throw new Error(`Couldn't reach the Silo backend at ${base}. Check your connection, or confirm the backend is deployed and reachable.`);
  }

  let body = null;
  try { body = await res.json(); } catch { /* empty/non-JSON body, e.g. some error pages */ }

  if (!res.ok) {
    let detail = (body && (body.detail || body.message)) || `Request failed (${res.status}).`;
    if (Array.isArray(detail)) {
      // FastAPI/pydantic 422 validation errors look like [{loc, msg, ...}, ...] — turn them into one readable sentence instead of dumping raw JSON on the person.
      detail = detail.map((d) => d.msg || JSON.stringify(d)).join(" ");
    } else if (typeof detail === "object" && detail !== null) {
      detail = JSON.stringify(detail);
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

function backendLogin(email, password) {
  return backendFetch("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) })
    .then((data) => data.access_token);
}

function backendRegister(email, password, fullName, phone, country) {
  return backendFetch("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name: fullName, phone, country }),
  });
}

/** Logs into the backend bridge account for `email`, registering it first
 * if it doesn't exist yet. Throws with a plain-English message (e.g. wrong
 * password for an email that already exists on the server) rather than
 * silently failing. On success, caches the token on state.user.backend. */
async function connectBackend(email, password, fullName, phone, country) {
  try {
    const token = await backendLogin(email, password);
    state.user.backend = { email, token };
    await saveProfile();
    return token;
  } catch (err) {
    if (err.status !== 401) throw err; // network/config error, or an unexpected server error — surface as-is
  }

  // 401 on login means either "not registered yet" or "wrong password for an
  // existing account" — try registering; if that 400s, it's definitely the
  // latter, and backendRegister's own error message says so plainly.
  await backendRegister(email, password, fullName, phone, country);
  const token = await backendLogin(email, password);
  state.user.backend = { email, token };
  await saveProfile();
  return token;
}

/** Fire-and-forget version used right after local login/register — never
 * blocks entering the app, and a failure here (most commonly: offline, or
 * this password doesn't match an existing backend account) just leaves
 * Payment Account showing its "connect" form for the person to retry later. */
function tryBootstrapBackendSession(email, password, fullName, phone, country) {
  connectBackend(email, password, fullName, phone, country)
    .then(() => { if (document.getElementById("payment-account-panel")) renderPaymentAccountPane(); })
    .catch((err) => console.info("Silo: backend not connected yet —", err.message));
}

function paymentAccountStatusBadge(status) {
  const map = { active: ["badge-success", "Active"], pending: ["badge-warning", "Pending"], failed: ["badge-danger", "Failed"] };
  const [cls, label] = map[status] || ["badge-neutral", status || "Unknown"];
  return `<span class="badge ${cls}">${label}</span>`;
}

async function requestPaymentAccount() {
  const phoneEl = document.getElementById("pa-phone");
  const phone = (phoneEl?.value || "").trim();
  if (!phone) { toast("Enter a phone number — Paystack requires one to create the account.", "danger"); return; }

  let token = state.user.backend?.token;
  if (!token) {
    const password = document.getElementById("pa-password")?.value || "";
    if (!password) { toast("Enter your Silo account password to connect.", "danger"); return; }
    try {
      token = await connectBackend(state.user.email, password, state.user.fullName, phone, state.user.country);
    } catch (err) {
      toast(err.message, "danger");
      return;
    }
  }

  state.user.phone = phone;
  await saveProfile();

  const btn = document.getElementById("pa-connect-btn") || document.getElementById("pa-create-btn");
  const originalLabel = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = "Working…"; }

  try {
    const account = await backendFetch("/payments/accounts", {
      method: "POST", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({ phone }),
    });
    state.user.paymentAccount = account;
    await saveProfile();
    toast(account.status === "active" ? "Payment account is active." : "Payment account requested — Paystack is generating your account number.", "success");
  } catch (err) {
    if (err.status === 502) toast("The payment provider isn't configured on the server yet (missing Paystack key).", "danger");
    else toast(err.message, "danger");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
    renderPaymentAccountPane();
  }
}

async function refreshPaymentAccountStatus() {
  const token = state.user.backend?.token;
  if (!token) { renderPaymentAccountPane(); return; }
  try {
    const accounts = await backendFetch("/payments/accounts", { headers: { Authorization: `Bearer ${token}` } });
    const priority = { active: 0, pending: 1, failed: 2 };
    const best = [...accounts].sort((a, b) => (priority[a.status] ?? 9) - (priority[b.status] ?? 9))[0];
    state.user.paymentAccount = best || null;
    await saveProfile();
    toast("Status updated.", "success");
  } catch (err) {
    toast(err.message, "danger");
  } finally {
    renderPaymentAccountPane();
  }
}

function renderPaymentAccountPane() {
  const panel = document.getElementById("payment-account-panel");
  if (!panel || !state.user) return;

  const connected = !!state.user.backend?.token;
  const account = state.user.paymentAccount;
  let html = "";

  if (!connected) {
    html = `
      <p class="text-secondary" style="font-size:12.5px;margin-bottom:12px;">Connect this device to your Silo account on the server (same email as this app) to request a payment account.</p>
      <div class="field"><label>Email</label><input class="input" value="${escapeHTML(state.user.email || "")}" disabled></div>
      <div class="field"><label for="pa-password">Password</label><input class="input" type="password" id="pa-password" placeholder="Your Silo account password"></div>
      <p class="text-secondary" style="font-size:11.5px;margin:-6px 0 12px;">If you don't have a Silo server account under this email yet, this creates one — needs at least 8 characters, including one digit.</p>
      <div class="field"><label for="pa-phone">Phone number (required by Paystack)</label><input class="input" id="pa-phone" placeholder="+2348012345678" value="${escapeHTML(state.user.phone || "")}"></div>
      <button class="btn btn-primary" id="pa-connect-btn">Connect & create payment account</button>`;
  } else if (!account) {
    html = `
      <div class="field"><label for="pa-phone">Phone number (required by Paystack)</label><input class="input" id="pa-phone" placeholder="+2348012345678" value="${escapeHTML(state.user.phone || "")}"></div>
      <button class="btn btn-primary" id="pa-create-btn">Create payment account</button>`;
  } else if (account.status === "pending") {
    html = `
      ${paymentAccountStatusBadge(account.status)}
      <p class="text-secondary" style="font-size:13px;margin-top:10px;">Paystack is generating your account number now — this usually takes a few moments. Check back shortly.</p>
      <button class="btn btn-secondary mt-8" id="pa-refresh-btn">Refresh status</button>`;
  } else if (account.status === "failed") {
    html = `
      ${paymentAccountStatusBadge(account.status)}
      <p class="text-secondary" style="font-size:13px;margin:10px 0;">${escapeHTML(account.failure_reason || "Something went wrong creating your account.")}</p>
      <div class="field"><label for="pa-phone">Phone number</label><input class="input" id="pa-phone" placeholder="+2348012345678" value="${escapeHTML(state.user.phone || "")}"></div>
      <button class="btn btn-primary" id="pa-create-btn">Try again</button>`;
  } else {
    const subline = [account.account_name, account.bank_name].filter(Boolean).map(escapeHTML).join(" · ");
    html = `
      ${paymentAccountStatusBadge(account.status)}
      <div style="margin-top:14px;">
        <div class="text-secondary" style="font-size:12px;">Account number</div>
        <div style="font-size:22px;font-weight:800;font-family:var(--font-display);letter-spacing:0.5px;">${escapeHTML(account.account_number || "—")}</div>
        <div class="text-secondary" style="font-size:12.5px;margin-top:4px;">${subline}</div>
      </div>
      <div class="flex gap-8 mt-16">
        <button class="btn btn-secondary" id="pa-copy-btn">Copy account number</button>
        <button class="btn btn-secondary" id="pa-refresh-btn">Refresh</button>
      </div>
      <p class="text-secondary" style="font-size:12px;margin-top:12px;">Share this account number with your employer or payer. Money sent here is automatically split across your envelopes using your most recently uploaded payslip's split.</p>`;
  }

  if (connected) {
    html += `<div class="mt-16"><a href="#" id="pa-disconnect-link" style="font-size:12px;color:var(--text-muted);">Disconnect this device</a></div>`;
  }

  panel.innerHTML = html;

  document.getElementById("pa-connect-btn")?.addEventListener("click", requestPaymentAccount);
  document.getElementById("pa-create-btn")?.addEventListener("click", requestPaymentAccount);
  document.getElementById("pa-refresh-btn")?.addEventListener("click", refreshPaymentAccountStatus);
  document.getElementById("pa-copy-btn")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(account?.account_number || ""); toast("Account number copied.", "success"); }
    catch { toast("Couldn't copy automatically — select and copy the number manually.", "danger"); }
  });
  document.getElementById("pa-disconnect-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    state.user.backend = null;
    state.user.paymentAccount = null;
    saveProfile();
    renderPaymentAccountPane();
  });
}

/* ---------------------------------------------------------------------- */
/* Misc utils                                                               */
/* ---------------------------------------------------------------------- */

function escapeHTML(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function applyTheme() {
  document.body.classList.toggle("light-mode", state.settings.theme === "light");
  document.body.classList.toggle("high-contrast", !!state.settings.highContrast);
}

/* ---------------------------------------------------------------------- */
/* PWA install prompt                                                       */
/* ---------------------------------------------------------------------- */

let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  maybeShowInstallBanner();
});

function maybeShowInstallBanner() {
  const dismissed = sessionStorage_safe_get("install-dismissed");
  if (deferredInstallPrompt && !dismissed) document.getElementById("install-banner").classList.add("show");
}
function sessionStorage_safe_get(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}
function sessionStorage_safe_set(key, val) {
  try { sessionStorage.setItem(key, val); } catch { /* no-op if storage unavailable */ }
}

/* ---------------------------------------------------------------------- */
/* Event wiring                                                             */
/* ---------------------------------------------------------------------- */

function wireEvents() {
  // Landing -> Auth
  document.querySelectorAll('[data-action="show-auth"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("view-landing").classList.remove("active");
      document.getElementById("view-auth").classList.add("active");
      setAuthTab(btn.dataset.mode);
    });
  });
  document.querySelectorAll('[data-action="scroll-to"]').forEach((btn) => {
    btn.addEventListener("click", () => document.getElementById(btn.dataset.target)?.scrollIntoView({ behavior: "smooth" }));
  });
  document.querySelectorAll('[data-auth-tab]').forEach((tab) => tab.addEventListener("click", () => setAuthTab(tab.dataset.authTab)));
  document.querySelectorAll('[data-action="oauth-stub"]').forEach((btn) => {
    btn.addEventListener("click", () => toast(`${btn.dataset.provider} sign-in is wired server-side (see backend/app/auth.py) — not available in this offline demo.`, "info"));
  });

  document.getElementById("form-login").addEventListener("submit", handleLogin);
  document.getElementById("form-register").addEventListener("submit", handleRegister);

  // App nav
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.addEventListener("click", () => {
      navigateApp(el.dataset.nav);
      if (el.dataset.settingsTab) setSettingsTab(el.dataset.settingsTab);
    });
  });
  document.querySelector('[data-action="logout"]').addEventListener("click", () => exitApp());

  // Settings tabs
  document.querySelectorAll("[data-settings-tab]").forEach((tab) => tab.addEventListener("click", () => setSettingsTab(tab.dataset.settingsTab)));

  // Modals
  document.querySelectorAll("[data-action='open-modal']").forEach((btn) => btn.addEventListener("click", () => openModal(btn.dataset.modal)));
  document.querySelectorAll("[data-action='close-modal']").forEach((btn) => btn.addEventListener("click", closeAllModals));
  document.querySelectorAll(".modal-overlay").forEach((overlay) => overlay.addEventListener("click", (e) => { if (e.target === overlay) closeAllModals(); }));

  // Upload
  document.querySelectorAll("[data-country]").forEach((chip) => chip.addEventListener("click", () => {
    document.querySelectorAll("[data-country]").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    state.selectedCountry = chip.dataset.country;
  }));
  document.getElementById("parse-btn").addEventListener("click", runParse);
  document.getElementById("confirm-allocate-btn").addEventListener("click", confirmAllocatePayslip);
  const dropzone = document.getElementById("dropzone");
  dropzone.addEventListener("click", () => document.getElementById("file-input").click());
  dropzone.addEventListener("keypress", (e) => { if (e.key === "Enter") document.getElementById("file-input").click(); });
  document.getElementById("file-input").addEventListener("change", (e) => handleFileSelected(e.target.files[0]));
  ["dragover", "dragleave", "drop"].forEach((evt) => dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.toggle("dragover", evt === "dragover");
    if (evt === "drop") handleFileSelected(e.dataTransfer.files[0]);
  }));
  document.getElementById("paste-area").addEventListener("paste", () => setTimeout(() => {}, 0));

  // Envelopes
  document.getElementById("create-env-confirm").addEventListener("click", async () => {
    const name = document.getElementById("new-env-name").value.trim();
    const color = document.getElementById("new-env-color").value;
    if (!name) { toast("Name your envelope.", "danger"); return; }
    if (Object.values(state.envelopes).find((e) => e.name === name)) { toast("An envelope with that name already exists.", "danger"); return; }
    await saveEnvelope({ id: genId(), name, balance: 0, allocated: 0, color, priority: 99, locked: false, archived: false, recurring: false });
    document.getElementById("new-env-name").value = "";
    toast("Envelope created.", "success");
    closeAllModals();
    renderEnvelopes(); renderDashboard();
  });

  // Transactions
  document.getElementById("add-txn-confirm").addEventListener("click", handleAddTransaction);
  document.getElementById("txn-search").addEventListener("input", renderTransactions);
  document.getElementById("txn-filter-type").addEventListener("change", renderTransactions);

  // Reports
  document.getElementById("report-period").addEventListener("change", renderReports);
  document.querySelector('[data-action="export-report"]').addEventListener("click", exportReportCSV);

  // Rules reset
  document.getElementById("reset-rules-btn").addEventListener("click", () => {
    state.rules = PEEngine.DEFAULT_ALLOCATION_RULES.map((r) => ({ ...r, id: genId() }));
    renderRulesModal();
    toast("Reset to Nigeria-first defaults.", "success");
  });

  // Settings
  document.getElementById("save-general-btn").addEventListener("click", async () => {
    state.settings.currency = document.getElementById("set-currency").value;
    state.settings.country = document.getElementById("set-country").value;
    state.settings.theme = document.getElementById("set-theme").value;
    state.settings.highContrast = document.getElementById("set-high-contrast").checked;
    if (state.user) { state.user.settings = state.settings; await saveProfile(); }
    applyTheme();
    toast("Settings saved.", "success");
    renderDashboard();
  });
  document.getElementById("enable-push-btn").addEventListener("click", async () => {
    if (!("Notification" in window)) { toast("Push notifications aren't supported in this browser.", "danger"); return; }
    const perm = await Notification.requestPermission();
    toast(perm === "granted" ? "Browser notifications enabled." : "Permission not granted.", perm === "granted" ? "success" : "info");
  });
  document.getElementById("export-data-btn").addEventListener("click", async () => {
    const payload = { user: state.user, envelopes: Object.values(state.envelopes), transactions: state.transactions, payslips: state.payslips, rules: state.rules };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "silo-export.json"; a.click();
    URL.revokeObjectURL(url);
    toast("Data exported.", "success");
  });
  document.getElementById("delete-account-btn").addEventListener("click", async () => {
    if (!confirm("This deletes all local Silo data on this device. Continue?")) return;
    await Storage.clearAll();
    location.reload();
  });

  // Install banner
  document.getElementById("install-btn").addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    document.getElementById("install-banner").classList.remove("show");
  });
  document.getElementById("install-dismiss").addEventListener("click", () => {
    document.getElementById("install-banner").classList.remove("show");
    sessionStorage_safe_set("install-dismissed", "1");
  });
}

function setAuthTab(mode) {
  document.querySelectorAll("[data-auth-tab]").forEach((t) => t.classList.toggle("active", t.dataset.authTab === mode));
  document.getElementById("form-login").style.display = mode === "login" ? "block" : "none";
  document.getElementById("form-register").style.display = mode === "register" ? "block" : "none";
}

function setSettingsTab(tab) {
  document.querySelectorAll("[data-settings-tab]").forEach((t) => t.classList.toggle("active", t.dataset.settingsTab === tab));
  document.querySelectorAll(".settings-pane").forEach((p) => p.style.display = p.dataset.settingsPane === tab ? "block" : "none");
  if (tab === "payment") renderPaymentAccountPane();
}

/* ---------------------------------------------------------------------- */
/* Boot                                                                      */
/* ---------------------------------------------------------------------- */

async function boot() {
  await loadState();
  wireEvents();
  applyTheme();
  if (state.user) enterApp();

  if ("serviceWorker" in navigator) {
    try { await navigator.serviceWorker.register("sw.js"); } catch (err) { console.warn("Service worker registration failed", err); }
  }
  navigator.serviceWorker?.addEventListener?.("message", (event) => {
    if (event.data?.type === "FLUSH_SYNC_QUEUE") { /* queued offline writes already land straight in IndexedDB in this build */ }
  });
}

document.addEventListener("DOMContentLoaded", boot);
