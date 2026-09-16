/**
 * A tiny IndexedDB key-value store for the hourly feeds (see hooks/useFeed.ts)
 * — this is where the page keeps its own copy of the last 48 hours, so each
 * entry is downloaded once and a reload starts from what the browser already
 * holds.
 *
 * IndexedDB rather than localStorage because the Message to Model feed can
 * reach several MB over 48 hours, past localStorage's ~5 MB cap. When
 * IndexedDB is unavailable (private browsing, blocked storage) every call
 * quietly does nothing and the page simply fetches again — never an error.
 */
const DB_NAME = "estate-dashboard";
const STORE_NAME = "feeds";

let databasePromise: Promise<IDBDatabase | null> | null = null;

function openDatabase(): Promise<IDBDatabase | null> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve) => {
    try {
      if (typeof indexedDB === "undefined") {
        resolve(null);
        return;
      }
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE_NAME)) {
          request.result.createObjectStore(STORE_NAME);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return databasePromise;
}

export interface StoredFeed<T> {
  cursor: string | null;
  items: T[];
}

export async function loadFeed<T>(key: string): Promise<StoredFeed<T> | null> {
  const database = await openDatabase();
  if (!database) return null;
  return new Promise((resolve) => {
    try {
      const request = database.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(key);
      request.onsuccess = () => {
        const value = request.result as StoredFeed<T> | undefined;
        resolve(value && Array.isArray(value.items) ? value : null);
      };
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

export async function saveFeed<T>(key: string, value: StoredFeed<T>): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  try {
    database.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).put(value, key);
  } catch {
    // Storage full or blocked: the page keeps working, it just re-fetches next time.
  }
}

/** Drops this browser's copy of one feed entirely — used by a page's Clear
 *  button alongside the matching backend reset, so a stale local copy can
 *  never repaint numbers the backend just zeroed. */
export async function deleteFeed(key: string): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  try {
    database.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).delete(key);
  } catch {
    // Storage full or blocked: nothing to clean up.
  }
}
