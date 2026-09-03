/** @odoo-module */

const DATABASE_NAME = "inari-device-stream-coordination";
const DATABASE_VERSION = 1;
const STORE_NAME = "candidates";
const CANDIDATE_TTL_MS = 4_000;

function base64Url(bytes) {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function openDatabase(indexedDBApi) {
    return new Promise((resolve, reject) => {
        const request = indexedDBApi.open(DATABASE_NAME, DATABASE_VERSION);
        request.addEventListener("upgradeneeded", () => {
            if (!request.result.objectStoreNames.contains(STORE_NAME)) {
                request.result.createObjectStore(STORE_NAME, { keyPath: "scope_digest" });
            }
        });
        request.addEventListener("success", () => resolve(request.result));
        request.addEventListener("error", () => reject(request.error));
    });
}

function candidateTransaction(database, scopeDigest, decide) {
    return new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE_NAME, "readwrite");
        const store = transaction.objectStore(STORE_NAME);
        let result = false;
        const request = store.get(scopeDigest);
        request.addEventListener("success", () => {
            const decision = decide(request.result || null);
            result = decision.result;
            if (decision.put) store.put(decision.put);
            if (decision.delete) store.delete(scopeDigest);
        });
        request.addEventListener("error", () => reject(request.error));
        transaction.addEventListener("complete", () => resolve(result));
        transaction.addEventListener("error", () => reject(transaction.error));
        transaction.addEventListener("abort", () => reject(transaction.error));
    });
}

/** Advisory IndexedDB coordination. The Agent-issued lease remains authoritative. */
export class IndexedDbTransportCandidateStore {
    constructor({ indexedDBApi = globalThis.indexedDB, now = Date.now } = {}) {
        if (!indexedDBApi) {
            throw new TypeError("IndexedDB is required for Device Stream coordination");
        }
        this.indexedDBApi = indexedDBApi;
        this.now = now;
    }

    async claim(scopeDigest, holderId) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            const now = this.now();
            return await candidateTransaction(database, scopeDigest, (current) => {
                if (current && current.expires_at > now && current.holder_id !== holderId) {
                    return { result: false };
                }
                return {
                    result: true,
                    put: {
                        scope_digest: scopeDigest,
                        holder_id: holderId,
                        expires_at: now + CANDIDATE_TTL_MS,
                    },
                };
            });
        } finally {
            database.close();
        }
    }

    async renew(scopeDigest, holderId) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            const now = this.now();
            return await candidateTransaction(database, scopeDigest, (current) => {
                if (!current || current.holder_id !== holderId || current.expires_at <= now) {
                    return { result: false };
                }
                return {
                    result: true,
                    put: { ...current, expires_at: now + CANDIDATE_TTL_MS },
                };
            });
        } finally {
            database.close();
        }
    }

    async release(scopeDigest, holderId) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            await candidateTransaction(database, scopeDigest, (current) => ({
                result: true,
                delete: current?.holder_id === holderId,
            }));
        } finally {
            database.close();
        }
    }
}

export class MemoryTransportCandidateStore {
    constructor({ now = Date.now } = {}) {
        this.now = now;
        this.records = new Map();
    }

    async claim(scopeDigest, holderId) {
        const now = this.now();
        const current = this.records.get(scopeDigest);
        if (current && current.expires_at > now && current.holder_id !== holderId) return false;
        this.records.set(scopeDigest, {
            scope_digest: scopeDigest,
            holder_id: holderId,
            expires_at: now + CANDIDATE_TTL_MS,
        });
        return true;
    }

    async renew(scopeDigest, holderId) {
        const current = this.records.get(scopeDigest);
        if (!current || current.holder_id !== holderId || current.expires_at <= this.now()) {
            return false;
        }
        current.expires_at = this.now() + CANDIDATE_TTL_MS;
        return true;
    }

    async release(scopeDigest, holderId) {
        if (this.records.get(scopeDigest)?.holder_id === holderId) {
            this.records.delete(scopeDigest);
        }
    }
}

class EmptyTransportHintChannel {
    subscribe() {
        return () => {};
    }

    publish() {}

    close() {}
}

export class BroadcastTransportHintChannel {
    constructor(
        scopeDigest,
        {
            BroadcastChannelApi = globalThis.BroadcastChannel,
            randomUUID = () => globalThis.crypto.randomUUID(),
        } = {},
    ) {
        if (!BroadcastChannelApi) return new EmptyTransportHintChannel();
        this.channel = new BroadcastChannelApi(`inari-device-stream:${scopeDigest}`);
        this.randomUUID = randomUUID;
    }

    subscribe(listener) {
        const receive = (event) => {
            const value = event.data;
            if (
                value?.contract_major === 1 &&
                ["candidate_changed", "lease_released", "reconcile_requested"].includes(
                    value.kind,
                ) &&
                typeof value.nonce === "string"
            ) {
                listener(value.kind);
            }
        };
        this.channel.addEventListener("message", receive);
        return () => this.channel.removeEventListener("message", receive);
    }

    publish(kind) {
        if (!["candidate_changed", "lease_released", "reconcile_requested"].includes(kind)) {
            throw new TypeError("Unknown Device Stream hint");
        }
        // BroadcastChannel.postMessage accepts one argument and has no target origin.
        /* oxlint-disable unicorn/require-post-message-target-origin */
        this.channel.postMessage({
            contract_major: 1,
            kind,
            nonce: this.randomUUID(),
        });
        /* oxlint-enable unicorn/require-post-message-target-origin */
    }

    close() {
        this.channel.close();
    }
}

export class MemoryTransportHintHub {
    constructor() {
        this.listeners = new Map();
    }

    channel(scopeDigest) {
        const listeners = this.listeners.get(scopeDigest) || new Set();
        this.listeners.set(scopeDigest, listeners);
        return {
            subscribe(listener) {
                listeners.add(listener);
                return () => listeners.delete(listener);
            },
            publish(kind) {
                for (const listener of listeners) listener(kind);
            },
            close() {},
        };
    }
}

export async function transportCoordinationDigest(value, cryptoApi = globalThis.crypto) {
    if (typeof value !== "string" || !value) {
        throw new TypeError("Device Stream coordination requires an opaque scope source");
    }
    const digest = await cryptoApi.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return base64Url(new Uint8Array(digest));
}

export const transportCandidateTiming = Object.freeze({ ttlMs: CANDIDATE_TTL_MS });
