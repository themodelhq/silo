/**
 * PayEnvelope Envelope Engine (client-side)
 * ===========================================
 * JavaScript port of backend/app/envelope_engine.py. Deterministic
 * percentage / fixed / remainder allocation rules — no ML, no AI.
 */

const AllocationType = { PERCENTAGE: "PERCENTAGE", FIXED: "FIXED", REMAINDER: "REMAINDER" };

const DEFAULT_ALLOCATION_RULES = [
  { envelopeName: "Rent", type: AllocationType.PERCENTAGE, value: 0.30, color: "#6366F1", priority: 1 },
  { envelopeName: "Transportation", type: AllocationType.PERCENTAGE, value: 0.15, color: "#38BDF8", priority: 2 },
  { envelopeName: "Savings", type: AllocationType.PERCENTAGE, value: 0.10, color: "#10B981", priority: 3 },
  { envelopeName: "Utilities", type: AllocationType.PERCENTAGE, value: 0.05, color: "#F59E0B", priority: 4 },
  { envelopeName: "Emergency Fund", type: AllocationType.PERCENTAGE, value: 0.05, color: "#EF4444", priority: 5 },
  { envelopeName: "Discretionary Spending", type: AllocationType.REMAINDER, value: 0.0, color: "#8B5CF6", priority: 6 },
];

const DEFAULT_ENVELOPE_CATALOG = [
  "Rent", "Food", "Transportation", "Fuel", "Utilities", "Internet", "Electricity",
  "Water", "DSTV", "Savings", "Emergency Fund", "Health", "Insurance", "Education",
  "Parents", "Giving", "Entertainment", "Shopping", "Travel", "Miscellaneous",
];

function uuid() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function validateRuleSet(rules) {
  const warnings = [];
  for (const r of rules) {
    if (r.type === AllocationType.PERCENTAGE && !(r.value >= 0 && r.value <= 1)) {
      throw new Error(`Percentage rule for '${r.envelopeName}' must be between 0 and 1, got ${r.value}`);
    }
    if (r.type === AllocationType.FIXED && r.value < 0) {
      throw new Error(`Fixed rule for '${r.envelopeName}' cannot be negative`);
    }
  }
  const pctTotal = rules.filter((r) => r.type === AllocationType.PERCENTAGE).reduce((s, r) => s + r.value, 0);
  const remainderRules = rules.filter((r) => r.type === AllocationType.REMAINDER);
  if (pctTotal > 1.0) warnings.push(`Percentage rules sum to ${(pctTotal * 100).toFixed(1)}%, which exceeds 100%.`);
  if (remainderRules.length > 1) warnings.push("More than one REMAINDER rule is defined; only the first will receive funds.");
  return warnings;
}

class EnvelopeEngine {
  constructor(rules) {
    this.rules = rules || DEFAULT_ALLOCATION_RULES.map((r) => ({ ...r, id: uuid() }));
    this.envelopes = {}; // name -> envelope object
    this.lastWarnings = [];
  }

  allocate(netSalary) {
    if (netSalary < 0) throw new Error("netSalary cannot be negative.");
    this.lastWarnings = validateRuleSet(this.rules);

    const ordered = [...this.rules].sort((a, b) => a.priority - b.priority);
    let remaining = netSalary;
    let remainderRule = null;

    for (const rule of ordered) {
      if (rule.type === AllocationType.REMAINDER) {
        if (!remainderRule) remainderRule = rule;
        continue;
      }
      let amount = rule.type === AllocationType.PERCENTAGE ? netSalary * rule.value : rule.value;
      amount = Math.round(Math.min(amount, Math.max(remaining, 0.0)) * 100) / 100;
      remaining = Math.round((remaining - amount) * 100) / 100;
      this._upsertEnvelope(rule.envelopeName, amount, rule.color, rule.priority);
    }

    if (remainderRule) {
      const amount = Math.round(Math.max(remaining, 0.0) * 100) / 100;
      this._upsertEnvelope(remainderRule.envelopeName, amount, remainderRule.color, remainderRule.priority);
    }

    return this.envelopes;
  }

  _upsertEnvelope(name, amount, color, priority) {
    let env = this.envelopes[name];
    if (!env) {
      env = { id: uuid(), name, balance: 0, allocated: 0, color, priority, locked: false, archived: false, recurring: false };
      this.envelopes[name] = env;
    }
    env.allocated = amount;
    env.balance = amount;
  }

  createEnvelope(name, color = "#6366F1", priority = 99) {
    if (this.envelopes[name]) throw new Error(`Envelope '${name}' already exists.`);
    const env = { id: uuid(), name, balance: 0, allocated: 0, color, priority, locked: false, archived: false, recurring: false };
    this.envelopes[name] = env;
    return env;
  }

  renameEnvelope(oldName, newName) {
    const env = this._require(oldName);
    env.name = newName;
    this.envelopes[newName] = env;
    delete this.envelopes[oldName];
  }

  deleteEnvelope(name) {
    const env = this._require(name);
    if (env.locked) throw new Error(`Envelope '${name}' is locked; unlock before deleting.`);
    delete this.envelopes[name];
  }

  archiveEnvelope(name) { this._require(name).archived = true; }
  unarchiveEnvelope(name) { this._require(name).archived = false; }
  lockEnvelope(name) { this._require(name).locked = true; }
  unlockEnvelope(name) { this._require(name).locked = false; }

  mergeEnvelopes(sourceName, targetName) {
    const source = this._require(sourceName);
    const target = this._require(targetName);
    target.balance = Math.round((target.balance + source.balance) * 100) / 100;
    target.allocated = Math.round((target.allocated + source.allocated) * 100) / 100;
    delete this.envelopes[sourceName];
    return target;
  }

  splitEnvelope(name, newName, fraction) {
    if (!(fraction > 0 && fraction < 1)) throw new Error("fraction must be strictly between 0 and 1.");
    const source = this._require(name);
    const movedBalance = Math.round(source.balance * fraction * 100) / 100;
    const movedAllocated = Math.round(source.allocated * fraction * 100) / 100;
    source.balance = Math.round((source.balance - movedBalance) * 100) / 100;
    source.allocated = Math.round((source.allocated - movedAllocated) * 100) / 100;
    const newEnv = { id: uuid(), name: newName, balance: movedBalance, allocated: movedAllocated, color: source.color, priority: source.priority, locked: false, archived: false, recurring: false };
    this.envelopes[newName] = newEnv;
    return [source, newEnv];
  }

  transfer(fromName, toName, amount) {
    const source = this._require(fromName);
    const target = this._require(toName);
    if (source.locked) throw new Error(`Envelope '${source.name}' is locked and cannot be spent from.`);
    if (amount < 0) throw new Error("Transfer amount must be non-negative.");
    source.balance = Math.round((source.balance - amount) * 100) / 100;
    target.balance = Math.round((target.balance + amount) * 100) / 100;
  }

  spend(name, amount) {
    const env = this._require(name);
    if (env.locked) throw new Error(`Envelope '${env.name}' is locked and cannot be spent from.`);
    env.balance = Math.round((env.balance - amount) * 100) / 100;
  }

  deposit(name, amount) {
    const env = this._require(name);
    env.balance = Math.round((env.balance + amount) * 100) / 100;
  }

  _require(name) {
    const env = this.envelopes[name];
    if (!env) throw new Error(`No envelope named '${name}'.`);
    return env;
  }
}

window.PayEnvelopeEngine = { EnvelopeEngine, AllocationType, DEFAULT_ALLOCATION_RULES, DEFAULT_ENVELOPE_CATALOG, validateRuleSet, uuid };
