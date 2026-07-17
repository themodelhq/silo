/**
 * Silo Frontend Config
 * ==============================
 * The app ships fully offline-first (IndexedDB only, no network calls) so
 * it works the moment it's deployed to Netlify with zero setup.
 *
 * If you want screens to also sync through the FastAPI backend deployed on
 * Render (JWT auth, server-side persistence, multi-device sync), set
 * API_BASE_URL below to your Render service's URL and wire the relevant
 * calls in js/app.js (e.g. swap the local-only handleLogin/handleRegister
 * for real fetch() calls to POST /auth/login and POST /auth/register).
 * That integration isn't done in this build — this file is just the single
 * place to point at your backend once you take that on.
 */
const SILO_CONFIG = {
  // Example: "https://silo-api.onrender.com"
  API_BASE_URL: "https://silo.onrender.com/",
};
