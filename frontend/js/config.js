/**
 * Silo Frontend Config
 * ==============================
 * The app ships offline-first (IndexedDB only) for everything except one
 * feature: Settings → Payment account, which requests a real bank account
 * number from the FastAPI backend (via Paystack). That's the only screen
 * that makes network calls. Everything else in the app (envelopes,
 * transactions, payslip parsing, reports) stays fully local to the device
 * regardless of this setting.
 *
 * API_BASE_URL below is the one place this is configured — there's no
 * user-facing setting for it, by design. To point the app at a different
 * backend, change this value and redeploy the frontend.
 *
 * Also make sure the backend's ALLOWED_ORIGINS environment variable
 * includes this site's origin (e.g. https://your-site.netlify.app) —
 * otherwise the browser will block the request as CORS-disallowed.
 */
const SILO_CONFIG = {
  API_BASE_URL: "https://silo-l86a.onrender.com",
};
