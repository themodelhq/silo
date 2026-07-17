/**
 * PayEnvelope Payslip Parser (client-side)
 * ==========================================
 * A line-for-line JavaScript port of backend/app/parser.py so the PWA can
 * parse payslips fully offline. No machine learning, no AI: every field is
 * extracted with a documented regular expression, and any missing total is
 * filled in with plain arithmetic — never inference.
 *
 * Keep this in sync with the Python engine if you change either one.
 */

const Country = { NIGERIA: "NG", GHANA: "GH", KENYA: "KE", SOUTH_AFRICA: "ZA" };

const COUNTRY_PROFILES = {
  [Country.NIGERIA]: {
    currencyCode: "NGN",
    statutoryLabels: {
      tax: "paye|pay\\s*as\\s*you\\s*earn|income\\s*tax|\\btax\\b",
      pension: "pension|pfa|rsa\\s*contribution",
      nhf: "nhf|national\\s*housing\\s*fund",
    },
  },
  [Country.GHANA]: {
    currencyCode: "GHS",
    statutoryLabels: {
      tax: "paye|income\\s*tax|\\btax\\b",
      pension: "ssnit|pension|tier\\s*[123]",
      nhf: "nhis",
    },
  },
  [Country.KENYA]: {
    currencyCode: "KES",
    statutoryLabels: {
      tax: "paye|income\\s*tax|\\btax\\b",
      pension: "nssf|pension",
      nhf: "shif|nhif",
    },
  },
  [Country.SOUTH_AFRICA]: {
    currencyCode: "ZAR",
    statutoryLabels: {
      tax: "paye|income\\s*tax|\\btax\\b",
      pension: "pension|provident\\s*fund",
      nhf: "uif",
    },
  },
};

const DEFAULT_COUNTRY = Country.NIGERIA;

const DETECTION_PATTERNS = {
  [Country.NIGERIA]: /₦|\bNGN\b|\bnaira\b/i,
  [Country.GHANA]: /GH₵|\bGHS\b|\bcedis\b|\bssnit\b/i,
  [Country.KENYA]: /\bKSh\b|\bKES\b|\bshillings\b|\bnssf\b/i,
  [Country.SOUTH_AFRICA]: /\bZAR\b|\brand\b|\buif\b|\bR\s?\d/i,
};

const NUM = "((?:\u20a6|GH\u20b5|KSh|NGN|GHS|KES|ZAR|R)?\\s?[\\d][\\d,]*\\.?\\d*)";

const FIELD_PATTERNS = {
  employeeName: /(?:employee\s*name|staff\s*name|name\s*of\s*employee|employee)\s*[:\-]\s*([A-Za-z][A-Za-z .'\-]{2,60})/i,
  employer: /^(.{3,80}?)(?:\n|LTD|LIMITED|PLC|NIGERIA|GHANA|KENYA)/i,
  basicSalary: new RegExp(`(?:basic|base)\\s*(?:salary|pay)?\\s*[:\\-=]?\\s*${NUM}`, "i"),
  housingAllowance: new RegExp(`(?:housing|rent)\\s*(?:allowance|pay)?\\s*[:\\-=]?\\s*${NUM}`, "i"),
  transportAllowance: new RegExp(`(?:transport|transportation|car)\\s*(?:allowance|pay)?\\s*[:\\-=]?\\s*${NUM}`, "i"),
  utilityAllowance: new RegExp(`(?:utility|utilities)\\s*(?:allowance)?\\s*[:\\-=]?\\s*${NUM}`, "i"),
  medicalAllowance: new RegExp(`(?:medical|health)\\s*(?:allowance)?\\s*[:\\-=]?\\s*${NUM}`, "i"),
  mealAllowance: new RegExp(`(?:meal|feeding|lunch)\\s*(?:allowance)?\\s*[:\\-=]?\\s*${NUM}`, "i"),
  bonus: new RegExp(`bonus\\s*[:\\-=]?\\s*${NUM}`, "i"),
  commission: new RegExp(`commission\\s*[:\\-=]?\\s*${NUM}`, "i"),
  otherDeductions: new RegExp(`(?:other\\s*deductions?|misc\\.?\\s*deductions?)\\s*[:\\-=]?\\s*${NUM}`, "i"),
  grossSalary: new RegExp(`(?:gross\\s*(?:salary|pay|income)?)\\s*[:\\-=]?\\s*${NUM}`, "i"),
  netSalary: new RegExp(`(?:net\\s*(?:pay|salary|amount|take\\s*home)|take\\s*home\\s*(?:pay)?)\\s*[:\\-=]?\\s*${NUM}`, "i"),
};

const MONTH_YEAR_PATTERN = /(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[,\s]+(\d{4})/i;
const MONTH_LOOKUP = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };

const ValidationStatus = {
  OK: "OK",
  CALCULATED: "CALCULATED",
  INCOMPLETE: "INCOMPLETE",
  REVIEW_REQUIRED: "REVIEW_REQUIRED",
};

function toNumber(raw) {
  if (!raw) return 0.0;
  const cleaned = raw.replace(/[^\d.]/g, "");
  if (!cleaned) return 0.0;
  const val = parseFloat(cleaned);
  return isNaN(val) ? 0.0 : Math.round(val * 100) / 100;
}

function searchField(text, pattern) {
  const match = text.match(pattern);
  return match ? match[1].trim() : null;
}

function detectCountry(text, fallback = DEFAULT_COUNTRY) {
  for (const [country, pattern] of Object.entries(DETECTION_PATTERNS)) {
    if (pattern.test(text)) return country;
  }
  return fallback;
}

function extractStatutoryDeductions(text, profile) {
  const results = { tax: 0.0, pension: 0.0, nhf: 0.0 };
  for (const [canonical, labelAlt] of Object.entries(profile.statutoryLabels)) {
    const pattern = new RegExp(`(?:${labelAlt})\\s*(?:\\(\\d+%?\\))?\\s*(?:deduction|contribution)?\\s*[:\\-=]?\\s*${NUM}`, "i");
    const val = searchField(text, pattern);
    if (val) results[canonical] = toNumber(val);
  }
  return results;
}

function extractMonthYear(text) {
  const match = text.match(MONTH_YEAR_PATTERN);
  if (!match) return { month: null, year: null };
  const monthKey = match[1].slice(0, 3).toLowerCase();
  return { month: MONTH_LOOKUP[monthKey] || null, year: parseInt(match[2], 10) };
}

/**
 * Parse free-form payslip text into a structured record. Mirrors the Python
 * `parse_payslip` pipeline exactly: normalize -> detect country -> extract
 * each field independently -> compute gross if missing -> compute net if
 * missing -> cross-check stated vs. computed net and flag mismatches.
 */
function parsePayslip(rawText, countryOverride = null) {
  const text = rawText.replace(/[ \t]+/g, " ").trim();
  const notes = [];

  const country = countryOverride || detectCountry(text);
  const profile = COUNTRY_PROFILES[country] || COUNTRY_PROFILES[DEFAULT_COUNTRY];

  const data = {
    employeeName: null,
    employer: null,
    payrollMonth: null,
    payrollYear: null,
    basicSalary: 0, housingAllowance: 0, transportAllowance: 0, utilityAllowance: 0,
    medicalAllowance: 0, mealAllowance: 0, bonus: 0, commission: 0,
    tax: 0, pension: 0, nhf: 0, otherDeductions: 0,
    grossSalary: 0, netSalary: 0,
    currency: profile.currencyCode, country,
    validationStatus: ValidationStatus.INCOMPLETE,
    extractionNotes: [],
  };

  data.employeeName = searchField(text, FIELD_PATTERNS.employeeName);
  const employerMatch = searchField(text, FIELD_PATTERNS.employer);
  data.employer = employerMatch ? employerMatch.trim() : null;

  const { month, year } = extractMonthYear(text);
  data.payrollMonth = month;
  data.payrollYear = year;

  data.basicSalary = toNumber(searchField(text, FIELD_PATTERNS.basicSalary));
  data.housingAllowance = toNumber(searchField(text, FIELD_PATTERNS.housingAllowance));
  data.transportAllowance = toNumber(searchField(text, FIELD_PATTERNS.transportAllowance));
  data.utilityAllowance = toNumber(searchField(text, FIELD_PATTERNS.utilityAllowance));
  data.medicalAllowance = toNumber(searchField(text, FIELD_PATTERNS.medicalAllowance));
  data.mealAllowance = toNumber(searchField(text, FIELD_PATTERNS.mealAllowance));
  data.bonus = toNumber(searchField(text, FIELD_PATTERNS.bonus));
  data.commission = toNumber(searchField(text, FIELD_PATTERNS.commission));

  const statutory = extractStatutoryDeductions(text, profile);
  data.tax = statutory.tax;
  data.pension = statutory.pension;
  data.nhf = statutory.nhf;
  data.otherDeductions = toNumber(searchField(text, FIELD_PATTERNS.otherDeductions));

  const explicitGross = toNumber(searchField(text, FIELD_PATTERNS.grossSalary));
  const computedGross = data.basicSalary + data.housingAllowance + data.transportAllowance +
    data.utilityAllowance + data.medicalAllowance + data.mealAllowance + data.bonus + data.commission;

  if (explicitGross > 0) {
    data.grossSalary = explicitGross;
    if (computedGross > 0 && Math.abs(explicitGross - computedGross) > Math.max(1.0, explicitGross * 0.01)) {
      notes.push(`Stated gross (${explicitGross.toLocaleString()}) differs from the sum of extracted earnings (${computedGross.toLocaleString()}); using the stated figure.`);
    }
  } else {
    data.grossSalary = computedGross;
    if (computedGross > 0) notes.push("Gross salary was not stated explicitly; computed as the sum of extracted earnings.");
  }

  const totalDeductions = data.tax + data.pension + data.nhf + data.otherDeductions;
  const explicitNet = toNumber(searchField(text, FIELD_PATTERNS.netSalary));
  const computedNet = Math.max(0.0, data.grossSalary - totalDeductions);

  if (explicitNet > 0) {
    data.netSalary = explicitNet;
    if (data.grossSalary > 0) {
      if (Math.abs(explicitNet - computedNet) <= Math.max(1.0, explicitNet * 0.01)) {
        data.validationStatus = ValidationStatus.OK;
      } else {
        data.validationStatus = ValidationStatus.REVIEW_REQUIRED;
        notes.push(`Stated net (${explicitNet.toLocaleString()}) does not reconcile with gross minus deductions (${computedNet.toLocaleString()}). Please review.`);
      }
    } else {
      data.validationStatus = ValidationStatus.OK;
    }
  } else if (computedNet > 0) {
    data.netSalary = computedNet;
    data.validationStatus = ValidationStatus.CALCULATED;
    notes.push("Net salary was not stated explicitly; calculated as gross minus deductions.");
  } else {
    data.validationStatus = ValidationStatus.INCOMPLETE;
    notes.push("Could not determine net salary: no explicit figure and insufficient earnings data.");
  }

  data.extractionNotes = notes;
  return data;
}

window.PayEnvelopeParser = { parsePayslip, detectCountry, Country, ValidationStatus, toNumber };
