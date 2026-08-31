/** @odoo-module */

import { contextKey } from "./submission_context";

const STORE_NAME = "submission_contexts";
const DB_NAME = "inari-device-context";

/** Content-free IndexedDB records survive a tab reload without storing receipt bytes. */
export class IndexedDbContextStore {
    constructor({ indexedDBApi = globalThis.indexedDB, databaseName = DB_NAME } = {}) {
        if (!indexedDBApi) {
            throw new Error("IndexedDB is required for Inari recovery");
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

    async put(context, state = "queued") {
        const database = await this.open();
        return new Promise((resolve, reject) => {
            const transaction = database.transaction(STORE_NAME, "readwrite");
            transaction.objectStore(STORE_NAME).put({
                key: contextKey(context),
                context,
                state,
                updated_at: new Date().toISOString(),
            });
            transaction.addEventListener("error", () => reject(transaction.error));
            transaction.addEventListener("complete", resolve);
        });
    }

    async get(key) {
        const database = await this.open();
        return new Promise((resolve, reject) => {
            const request = database.transaction(STORE_NAME).objectStore(STORE_NAME).get(key);
            request.addEventListener("error", () => reject(request.error));
            request.addEventListener("success", () => resolve(request.result || null));
        });
    }

    async list() {
        const database = await this.open();
        return new Promise((resolve, reject) => {
            const request = database.transaction(STORE_NAME).objectStore(STORE_NAME).getAll();
            request.addEventListener("error", () => reject(request.error));
            request.addEventListener("success", () => resolve(request.result));
        });
    }

    async remove(key) {
        const database = await this.open();
        return new Promise((resolve, reject) => {
            const transaction = database.transaction(STORE_NAME, "readwrite");
            transaction.objectStore(STORE_NAME).delete(key);
            transaction.addEventListener("error", () => reject(transaction.error));
            transaction.addEventListener("complete", resolve);
        });
    }
}

/** Deterministic store for unit tests and non-browser composition tests. */
export class MemoryContextStore {
    constructor() {
        this.records = new Map();
    }

    async put(context, state = "queued") {
        this.records.set(contextKey(context), {
            key: contextKey(context),
            context,
            state,
            updated_at: new Date().toISOString(),
        });
    }

    async get(key) {
        return this.records.get(key) || null;
    }

    async list() {
        return [...this.records.values()];
    }

    async remove(key) {
        this.records.delete(key);
    }
}
