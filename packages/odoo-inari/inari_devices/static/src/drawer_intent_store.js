/** @odoo-module */

const DATABASE_NAME = "inari-drawer-intents";
const DATABASE_VERSION = 1;
const STORE_NAME = "active_intents";
const RETENTION_MS = 90 * 24 * 60 * 60 * 1_000;

function requestResult(request) {
    return new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result ?? null);
        request.onerror = () => reject(request.error);
    });
}

function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error || new Error("Drawer Intent storage aborted"));
    });
}

function openDatabase(indexedDBApi) {
    return new Promise((resolve, reject) => {
        const request = indexedDBApi.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(STORE_NAME)) {
                request.result.createObjectStore(STORE_NAME, { keyPath: "scope_key" });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

export class IndexedDbDrawerIntentStore {
    constructor({ indexedDBApi = globalThis.indexedDB, now = Date.now } = {}) {
        if (!indexedDBApi) {
            throw new TypeError("IndexedDB is required for Drawer Intent recovery");
        }
        this.indexedDBApi = indexedDBApi;
        this.now = now;
    }

    async get(scopeKey) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            const record = await requestResult(
                database.transaction(STORE_NAME).objectStore(STORE_NAME).get(scopeKey),
            );
            if (!record || record.saved_at + RETENTION_MS > this.now()) {
                return record;
            }
        } finally {
            database.close();
        }
        await this.delete(scopeKey);
        return null;
    }

    async put(record) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            const transaction = database.transaction(STORE_NAME, "readwrite");
            const done = transactionDone(transaction);
            transaction.objectStore(STORE_NAME).put(Object.freeze({ ...record, saved_at: this.now() }));
            await done;
        } finally {
            database.close();
        }
    }

    async delete(scopeKey) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            const transaction = database.transaction(STORE_NAME, "readwrite");
            const done = transactionDone(transaction);
            transaction.objectStore(STORE_NAME).delete(scopeKey);
            await done;
        } finally {
            database.close();
        }
    }
}

export class MemoryDrawerIntentStore {
    constructor() {
        this.records = new Map();
    }

    async get(scopeKey) {
        return this.records.get(scopeKey) || null;
    }

    async put(record) {
        this.records.set(record.scope_key, Object.freeze({ ...record }));
    }

    async delete(scopeKey) {
        this.records.delete(scopeKey);
    }
}
