/**
 * Silo Frontend Config
 * ==============================
 * The app ships offline-first (IndexedDB only) for everything except one
 * feature: Settings → Payment account, which requests a real bank account
 * number from the FastAPI backend (via Paystack). That's the only screen
 * that makes network calls — set API_BASE_URL below to your deployed
 * backend's URL (e.g. the Render service from backend/README.md) for it to
 * work. Everything else in the app (envelopes, transactions, payslip
 * parsing, reports) stays fully local to the device regardless of this
 * setting.
 *
 * Also make sure the backend's ALLOWED_ORIGINS environment variable
 * includes this site's origin (e.g. https://your-site.netlify.app) —
 * otherwise the browser will block the request as CORS-disallowed.
 */
const SILO_CONFIG = {
  // Example: "https://silo-api.onrender.com"
  API_BASE_URL: "https://silo-l86a.onrender.com/",
};
