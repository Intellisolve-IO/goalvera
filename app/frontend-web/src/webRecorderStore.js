/** Audio stays in IndexedDB only until the server confirms that piece. Then it is deleted. */

const DB = 'goalvera-web-recorder';
const VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('segments')) db.createObjectStore('segments');
      if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta');
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function reqDone(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export function idbStore() {
  let dbp = null;
  const db = () => { dbp = dbp || openDb(); return dbp; };
  return {
    async saveMeta(meta) {
      const base = await db();
      const tx = base.transaction('meta', 'readwrite');
      tx.objectStore('meta').put(meta, 'current');
      return txDone(tx);
    },
    async loadMeta() {
      const base = await db();
      return reqDone(base.transaction('meta').objectStore('meta').get('current'));
    },
    async putSegment(sessionId, index, rec) {
      const base = await db();
      const tx = base.transaction('segments', 'readwrite');
      tx.objectStore('segments').put(rec, `${sessionId}:${index}`);
      return txDone(tx);
    },
    async getSegment(sessionId, index) {
      const base = await db();
      return reqDone(base.transaction('segments').objectStore('segments').get(`${sessionId}:${index}`));
    },
    async deleteSegment(sessionId, index) {
      const base = await db();
      const tx = base.transaction('segments', 'readwrite');
      tx.objectStore('segments').delete(`${sessionId}:${index}`);
      return txDone(tx);
    },
    async listSegments() {
      const base = await db();
      const all = await reqDone(base.transaction('segments').objectStore('segments').getAll());
      return all || [];
    },
    async clear() {
      const base = await db();
      const tx = base.transaction(['segments', 'meta'], 'readwrite');
      tx.objectStore('segments').clear();
      tx.objectStore('meta').clear();
      return txDone(tx);
    },
  };
}

/** Sign-out and discard: drop the database, including any piece still waiting. */
export function clearLocalAudio() {
  return new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(); return; }
    const req = indexedDB.deleteDatabase(DB);
    req.onsuccess = () => resolve();
    req.onerror = () => resolve();
    req.onblocked = () => resolve();
  });
}

/** In-memory stand-in for tests. */
export function memoryStore() {
  const segs = new Map();
  let meta = null;
  return {
    async saveMeta(m) { meta = m ? JSON.parse(JSON.stringify(m)) : null; },
    async loadMeta() { return meta ? JSON.parse(JSON.stringify(meta)) : null; },
    async putSegment(sessionId, index, rec) { segs.set(`${sessionId}:${index}`, rec); },
    async getSegment(sessionId, index) { return segs.get(`${sessionId}:${index}`) || null; },
    async deleteSegment(sessionId, index) { segs.delete(`${sessionId}:${index}`); },
    async listSegments() { return [...segs.values()]; },
    async clear() { segs.clear(); meta = null; },
  };
}
