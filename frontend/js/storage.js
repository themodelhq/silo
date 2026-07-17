/**
 * PayEnvelope Storage Layer
 * ===========================
 * Wraps IndexedDB for durable offline-first storage of everything the app
 * needs: user profile, payslips, envelopes, allocation rules, transactions,
 * bills, and goals. Nothing here ever leaves the device unless the person
 * explicitly exports it — this is a local-first demo data layer, not a
 * network client.
 */

const DB_NAME = "payenvelope";
const DB_VERSION = 1;
const STORES = ["profile", "payslips", "envelopes", "rules", "transactions", "bills", "goals", "notifications"];

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      STORES.forEach((name) => {
        if (!db.objectStoreNames.contains(name)) {
          db.createObjectStore(name, { keyPath: "id" });
        }
      });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
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

window.PayEnvelopeStorage = { put, getAll, get, remove, clearStore, clearAll, STORES };
