/** @odoo-module */

import { contextKey } from "./submission_context";

const TERMINAL_STATES = new Set(["accepted", "canceled", "expired", "outcome_unknown"]);

/** Serialize receipt admission while keeping each rendered document in memory. */
export class ReceiptQueue {
    constructor({ contextStore, submit }) {
        if (!contextStore || typeof submit !== "function") {
            throw new TypeError("ReceiptQueue requires a context store and submit function");
        }
        this.contextStore = contextStore;
        this.submit = submit;
        this.entries = [];
        this.drainPromise = null;
        this.listeners = new Set();
    }

    onChange(listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    notify() {
        const snapshot = this.snapshot();
        for (const listener of this.listeners) {
            listener(snapshot);
        }
    }

    async enqueue(context, jpeg) {
        if (!context || !Object.isFrozen(context) || !(jpeg instanceof Blob)) {
            throw new TypeError(
                "A receipt queue entry requires an immutable context and JPEG Blob",
            );
        }
        const entry = {
            key: contextKey(context),
            context,
            jpeg,
            state: "queued",
            result: null,
            error: null,
        };
        this.entries.push(entry);
        await this.contextStore.put(context, entry.state);
        this.notify();
        await this.drain();
        return entry;
    }

    async drain() {
        if (this.drainPromise) {
            return this.drainPromise;
        }
        this.drainPromise = this.drainEntries();
        try {
            await this.drainPromise;
        } finally {
            this.drainPromise = null;
        }
    }

    async drainEntries() {
        // Receipt admission is serial because physical copy order is part of its identity.
        for (const entry of this.entries) {
            if (TERMINAL_STATES.has(entry.state) || entry.state === "submitting") {
                continue;
            }
            entry.state = "submitting";
            entry.error = null;
            // oxlint-disable-next-line no-await-in-loop
            await this.contextStore.put(entry.context, entry.state);
            this.notify();
            try {
                // oxlint-disable-next-line no-await-in-loop
                entry.result = await this.submit(entry.context, entry.jpeg);
                entry.state = entry.result?.state || "accepted";
                if (entry.state === "accepted") {
                    // oxlint-disable-next-line no-await-in-loop
                    await this.contextStore.remove(entry.key);
                } else {
                    // oxlint-disable-next-line no-await-in-loop
                    await this.contextStore.put(entry.context, entry.state);
                }
            } catch (error) {
                entry.error = error;
                entry.state = "failed";
                // oxlint-disable-next-line no-await-in-loop
                await this.contextStore.put(entry.context, entry.state);
            }
            this.notify();
        }
    }

    async retry(key) {
        const entry = this.entries.find((candidate) => candidate.key === key);
        if (!entry) {
            throw new Error("Receipt context is not available in this tab");
        }
        if (entry.state === "accepted") {
            return entry.result;
        }
        if (this.drainPromise) {
            await this.drain();
            if (entry.state === "accepted") {
                return entry.result;
            }
        }
        entry.state = "queued";
        entry.error = null;
        await this.contextStore.put(entry.context, entry.state);
        this.notify();
        await this.drain();
        return entry.result;
    }

    snapshot() {
        return this.entries.map((entry) => ({
            key: entry.key,
            context: entry.context,
            state: entry.state,
            result: entry.result,
            error: entry.error,
        }));
    }
}
