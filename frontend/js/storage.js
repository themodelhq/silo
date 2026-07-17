/**
 * Silo Storage Layer
 * ===========================
 * Wraps IndexedDB for durable offline-first storage of everything the app
 * needs: user profile, payslips, envelopes, allocation rules, transactions,
 * bills, and goals. Nothing here ever leaves the device unless the person
 * explicitly exports it — this is a local-first demo data layer, not a
 * network client.
 */

const DB_NAME = "silo";
const LEGACY_DB_NAME = "payenvelope"; // pre-rebrand database name
const DB_VERSION = 1;
const STORES = ["profile", "payslips", "envelopes", "rules", "transactions", "bills", "goals", "notifications"];
const MIGRATION_FLAG_KEY = "silo:migrated-from-payenvelope";

function openNamedDB(name, version) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(name, version);
    req.onupgradeneeded = () => {
      const db = req.result;
      STORES.forEach((storeName) => {
        if (!db.objectStoreNames.contains(storeName)) {
          db.createObjectStore(storeName, { keyPath: "id" });
        }
      });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function openDB() {
  return openNamedDB(DB_NAME, DB_VERSION);
}

function readAllFromDb(db, storeName) {
  return new Promise((resolve, reject) => {
    try {
      const tx = db.transaction(storeName, "readonly");
      const req = tx.objectStore(storeName).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    } catch (err) {
      // Store may not exist in an older/legacy database — treat as empty.
      resolve([]);
    }
  });
}

/**
 * One-time, best-effort copy of data from the pre-rebrand "payenvelope"
 * IndexedDB into the new "silo" one, so renaming the app doesn't silently
 * orphan anything a person already saved on this device. Safe to call on
 * every load: it no-ops once MIGRATION_FLAG_KEY is set, and it only copies
 * into stores that are still empty (never overwrites newer data).
 */
async function migrateFromLegacyDatabaseIfNeeded() {
  try {
    if (localStorage.getItem(MIGRATION_FLAG_KEY)) return;

    // Only attempt this if we can tell the legacy database actually exists —
    // otherwise `indexedDB.open` would silently create an empty one.
    if (typeof indexedDB.databases === "function") {
      const existing = await indexedDB.databases();
      const hasLegacy = existing.some((d) => d.name === LEGACY_DB_NAME);
      if (!hasLegacy) {
        localStorage.setItem(MIGRATION_FLAG_KEY, "1");
        return;
      }
    }

    const [legacyDb, newDb] = await Promise.all([openNamedDB(LEGACY_DB_NAME, DB_VERSION), openDB()]);

    for (const storeName of STORES) {
      const alreadyHasData = (await readAllFromDb(newDb, storeName)).length > 0;
      if (alreadyHasData) continue;

      const legacyRecords = await readAllFromDb(legacyDb, storeName);
      if (!legacyRecords.length) continue;

      await new Promise((resolve, reject) => {
        const tx = newDb.transaction(storeName, "readwrite");
        const store = tx.objectStore(storeName);
        legacyRecords.forEach((record) => store.put(record));
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    localStorage.setItem(MIGRATION_FLAG_KEY, "1");
  } catch (err) {
    // Best-effort only — never block the app on a migration failure.
    console.warn("Silo: legacy data migration skipped:", err);
  }
}

async function withStore(storeName, mode, fn) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const store = tx.objectStore(storeName);
    const result = fn(store);
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error);
  });
}

async function put(storeName, record) {
  await withStore(storeName, "readwrite", (store) => store.put(record));
  return record;
}

async function getAll(storeName) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readonly");
    const req = tx.objectStore(storeName).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function get(storeName, id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readonly");
    const req = tx.objectStore(storeName).get(id);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function remove(storeName, id) {
  await withStore(storeName, "readwrite", (store) => store.delete(id));
}

async function clearStore(storeName) {
  await withStore(storeName, "readwrite", (store) => store.clear());
}

async function clearAll() {
  for (const s of STORES) await clearStore(s);
}

migrateFromLegacyDatabaseIfNeeded();

window.SiloStorage = { put, getAll, get, remove, clearStore, clearAll, STORES };
