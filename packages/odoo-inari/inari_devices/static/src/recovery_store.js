/** @odoo-module */

const STORE_NAME = "tasks";
const DATABASE_NAME = "inari-print-recovery";

function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
        transaction.addEventListener("complete", resolve);
        transaction.addEventListener("error", () => reject(transaction.error));
        transaction.addEventListener("abort", () => reject(transaction.error));
    });
}

/** Persist content-free recovery tasks without receipt or driver data. */
export class IndexedDbRecoveryStore {
    constructor({ indexedDBApi = globalThis.indexedDB, databaseName = DATABASE_NAME } = {}) {
        if (!indexedDBApi) {
            throw new Error("IndexedDB is required for Inari print recovery");
        }
        this.indexedDBApi = indexedDBApi;
        this.databaseName = databaseName;
        this.databasePromise = null;
    }

    async open() {
        if (!this.databasePromise) {
            this.databasePromise = new Promise((resolve, reject) => {
                const request = this.indexedDBApi.open(this.databaseName, 1);
                request.addEventListener("error", () => reject(request.error));
                request.addEventListener("upgradeneeded", () => {
                    request.result.createObjectStore(STORE_NAME, { keyPath: "key" });
                });
                request.addEventListener("success", () => resolve(request.result));
            });
        }
        return this.databasePromise;
    }

    async put(record) {
        const database = await this.open();
        const transaction = database.transaction(STORE_NAME, "readwrite");
        transaction.objectStore(STORE_NAME).put(structuredClone(record));
        await transactionDone(transaction);
    }

    async list() {
        const database = await this.open();
        return new Promise((resolve, reject) => {
            const request = database.transaction(STORE_NAME).objectStore(STORE_NAME).getAll();
            request.addEventListener("error", () => reject(request.error));
            request.addEventListener("success", () => resolve(request.result));
        });
    }
}

/** Deterministic recovery store for browser-independent tests. */
export class MemoryRecoveryStore {
    constructor(records = []) {
        this.records = new Map(records.map((record) => [record.key, structuredClone(record)]));
    }

    async put(record) {
        this.records.set(record.key, structuredClone(record));
    }

    async list() {
        return [...this.records.values()].map((record) => structuredClone(record));
    }
}
