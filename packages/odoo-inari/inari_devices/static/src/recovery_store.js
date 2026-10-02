/** @odoo-module */

const TASKS = "tasks";
const SETTLED = "settled";
const DATABASE_NAME = "inari-print-recovery";
const RETENTION_MS = 90 * 24 * 60 * 60 * 1_000;

export function receiptIdentityKey(plan) {
    return JSON.stringify([
        plan.pos_session_id, plan.offline_order_id, plan.binding_revision_id,
        plan.device_id, plan.copy_ordinal,
    ]);
}

function archive(record, now) {
    return {
        ...record,
        receipt_identity: receiptIdentityKey({ ...record.context, ...record.context.origin }),
        expires_at: now + RETENTION_MS,
    };
}

function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
        transaction.addEventListener("complete", resolve);
        transaction.addEventListener("error", () => reject(transaction.error));
        transaction.addEventListener("abort", () => reject(transaction.error));
    });
}

function requestResult(request) {
    return new Promise((resolve, reject) => {
        request.addEventListener("error", () => reject(request.error));
        request.addEventListener("success", () => resolve(request.result));
    });
}

/** Keep active recovery tasks separate from the bounded receipt identity journal. */
export class IndexedDbRecoveryStore {
    constructor({ indexedDBApi = globalThis.indexedDB, databaseName = DATABASE_NAME, now = Date.now } = {}) {
        if (!indexedDBApi) throw new Error("IndexedDB is required for Inari print recovery");
        this.indexedDBApi = indexedDBApi;
        this.databaseName = databaseName;
        this.now = now;
        this.databasePromise = null;
    }

    async open() {
        if (!this.databasePromise) {
            this.databasePromise = new Promise((resolve, reject) => {
                const request = this.indexedDBApi.open(this.databaseName, 2);
                request.addEventListener("error", () => reject(request.error));
                request.addEventListener("upgradeneeded", () => {
                    const database = request.result;
                    if (!database.objectStoreNames.contains(TASKS)) {
                        database.createObjectStore(TASKS, { keyPath: "key" });
                    }
                    const settled = database.createObjectStore(SETTLED, { keyPath: "key" });
                    settled.createIndex("receipt_identity", "receipt_identity");
                    settled.createIndex("expires_at", "expires_at");
                });
                request.addEventListener("success", () => resolve(request.result));
            });
        }
        return this.databasePromise;
    }

    async put(record) {
        const database = await this.open();
        const transaction = database.transaction(TASKS, "readwrite");
        const done = transactionDone(transaction);
        transaction.objectStore(TASKS).put(structuredClone(record));
        await done;
    }

    async settle(record) {
        const database = await this.open();
        const transaction = database.transaction([TASKS, SETTLED], "readwrite");
        const done = transactionDone(transaction);
        transaction.objectStore(SETTLED).put(archive(record, this.now()));
        transaction.objectStore(TASKS).delete(record.key);
        await done;
        await this.prune();
    }

    async prune() {
        const database = await this.open();
        const transaction = database.transaction(SETTLED, "readwrite");
        const done = transactionDone(transaction);
        const request = transaction.objectStore(SETTLED).index("expires_at").openCursor();
        const cutoff = this.now();
        request.addEventListener("success", () => {
            const cursor = request.result;
            if (cursor && cursor.key <= cutoff) {
                cursor.delete();
                cursor.continue();
            }
        });
        await done;
    }

    async getSettled(key) {
        const database = await this.open();
        const record = await requestResult(database.transaction(SETTLED).objectStore(SETTLED).get(key));
        return record?.expires_at > this.now() ? record : null;
    }

    async receiptContext(plan) {
        const database = await this.open();
        const records = await requestResult(database.transaction(SETTLED).objectStore(SETTLED)
            .index("receipt_identity").getAll(receiptIdentityKey(plan)));
        const current = records.filter((record) => record.expires_at > this.now());
        if (current.length > 1) throw new TypeError("This receipt copy has conflicting recovery identities");
        return current[0]?.context || null;
    }

    async list() {
        await this.prune();
        const database = await this.open();
        return requestResult(database.transaction(TASKS).objectStore(TASKS).getAll());
    }
}

/** Deterministic recovery store for browser-independent tests. */
export class MemoryRecoveryStore {
    constructor(records = [], { now = Date.now } = {}) {
        this.records = new Map(records.map((record) => [record.key, structuredClone(record)]));
        this.settled = new Map();
        this.now = now;
    }

    async put(record) {
        this.records.set(record.key, structuredClone(record));
    }

    async settle(record) {
        this.settled.set(record.key, structuredClone(archive(record, this.now())));
        this.records.delete(record.key);
        await this.prune();
    }

    async prune() {
        for (const [key, record] of this.settled) {
            if (record.expires_at <= this.now()) this.settled.delete(key);
        }
    }

    async getSettled(key) {
        await this.prune();
        return structuredClone(this.settled.get(key) || null);
    }

    async receiptContext(plan) {
        await this.prune();
        const records = [...this.settled.values()].filter(
            (record) => record.receipt_identity === receiptIdentityKey(plan),
        );
        if (records.length > 1) throw new TypeError("This receipt copy has conflicting recovery identities");
        return structuredClone(records[0]?.context || null);
    }

    async list() {
        await this.prune();
        return [...this.records.values()].map((record) => structuredClone(record));
    }
}
