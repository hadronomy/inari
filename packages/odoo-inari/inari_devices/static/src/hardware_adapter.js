/** @odoo-module */

import { InariAgentError } from "./agent_client";
import { IndexedDbDrawerIntentStore, MemoryDrawerIntentStore } from "./drawer_intent_store";

const TERMINAL_STATES = new Set(["succeeded", "failed", "outcome_unknown"]);
const POLL_INTERVAL_MS = 250;
const POLL_ATTEMPTS = 24;

function controlledResult(intent, values) {
    return Object.freeze({
        ...values,
        drawer_intent_id: intent.drawer_intent_id,
        accepted: values.state === "succeeded",
        retryable: values.retryable === true,
    });
}

function serverResult(intent, value) {
    if (
        !value ||
        value.drawer_intent_id !== intent.drawer_intent_id ||
        value.device_id !== intent.device_id
    ) {
        throw new TypeError("The Agent returned the wrong Drawer Intent identity");
    }
    if (
        !["accepted", "in_progress", "succeeded", "failed", "outcome_unknown"].includes(value.state)
    ) {
        throw new TypeError("The Agent returned an invalid Drawer Intent state");
    }
    return controlledResult(intent, {
        ...value,
        retryable: value.retryable === true,
    });
}

/** Owns bound POS hardware policy. Odoo patches call this small interface only. */
export class InariHardwareAdapter {
    constructor({
        clientForBinding,
        randomUUID = () => crypto.randomUUID(),
        now = Date.now,
        store = globalThis.indexedDB
            ? new IndexedDbDrawerIntentStore({ now })
            : new MemoryDrawerIntentStore(),
        sleep = (milliseconds) =>
            new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds)),
    } = {}) {
        if (typeof clientForBinding !== "function") {
            throw new TypeError("InariHardwareAdapter requires a Client resolver");
        }
        this.clientForBinding = clientForBinding;
        this.randomUUID = randomUUID;
        this.now = now;
        this.store = store;
        this.sleep = sleep;
        this.binding = null;
        this.posSessionId = null;
        this.lastSequence = 0;
        this.retryableIntent = null;
        this.uncertainIntent = null;
        this.scopeKey = null;
        this.journalAvailable = true;
    }

    async attach({ binding, posSessionId }) {
        this.binding = binding?.authoritative === true ? Object.freeze({ ...binding }) : null;
        this.posSessionId = String(posSessionId);
        this.retryableIntent = null;
        this.uncertainIntent = null;
        this.scopeKey = this.binding
            ? [
                  "v1",
                  this.binding.database,
                  this.binding.company_id,
                  this.binding.pos_configuration_id,
                  this.posSessionId,
                  this.binding.binding_revision_id,
                  this.binding.device_id,
              ].join("|")
            : null;
        let saved = null;
        try {
            saved = this.scopeKey ? await this.store.get(this.scopeKey) : null;
            this.journalAvailable = true;
        } catch {
            this.journalAvailable = false;
        }
        if (saved?.state === "retryable") {
            this.retryableIntent = Object.freeze({ ...saved.intent });
        } else if (saved?.state === "outcome_unknown") {
            this.uncertainIntent = Object.freeze({ ...saved.intent });
        }
    }

    async openDrawer({ action = false } = {}) {
        if (!this.binding) {
            return controlledResult(
                { drawer_intent_id: "unbound" },
                { state: "failed", error_code: "not_configured" },
            );
        }
        if (!this.journalAvailable) {
            return controlledResult(
                { drawer_intent_id: "journal-unavailable" },
                {
                    state: "failed",
                    retryable: false,
                    error_code: "drawer_journal_unavailable",
                },
            );
        }
        if (this.uncertainIntent) {
            return controlledResult(this.uncertainIntent, {
                state: "outcome_unknown",
                error_code: "manager_review_required",
            });
        }

        const intent = this.retryableIntent || this.createIntent(action);
        if (!this.retryableIntent && !(await this.remember("retryable", intent))) {
            return this.journalUnavailable(intent);
        }
        let client;
        try {
            client = await this.clientForBinding(this.binding, { interactive: true });
        } catch (error) {
            return this.failBeforeIo(intent, error);
        }
        if (!client) {
            return this.failBeforeIo(
                intent,
                new InariAgentError(
                    "pairing_required",
                    "Pair this browser before opening the drawer.",
                ),
            );
        }

        try {
            const submitted = serverResult(intent, await client.submitDrawerIntent(intent));
            return await this.waitForTerminal(client, intent, submitted);
        } catch (error) {
            if (error instanceof InariAgentError && error.status && error.status < 500) {
                return this.failBeforeIo(intent, error);
            }
            return this.reconcileUncertain(client, intent, error);
        }
    }

    createIntent(action) {
        const sequence = Math.max(this.lastSequence + 1, Number(this.now()));
        if (!Number.isSafeInteger(sequence) || sequence < 1) {
            throw new TypeError("The Drawer Intent action sequence is invalid");
        }
        this.lastSequence = sequence;
        return Object.freeze({
            contract_major: 1,
            drawer_intent_id: `drawer-${this.randomUUID()}`,
            binding_revision_id: this.binding.binding_revision_id,
            device_id: this.binding.device_id,
            pos_session_id: this.posSessionId,
            action_sequence: sequence,
            reason: action ? "manual_open" : "payment",
        });
    }

    async waitForTerminal(client, intent, initial) {
        let result = initial;
        for (
            let attempt = 0;
            attempt < POLL_ATTEMPTS && !TERMINAL_STATES.has(result.state);
            attempt++
        ) {
            // oxlint-disable-next-line no-await-in-loop
            await this.sleep(POLL_INTERVAL_MS);
            // oxlint-disable-next-line no-await-in-loop
            const page = await client.queryDrawerIntents([intent.drawer_intent_id]);
            const value = page.intents?.[0];
            if (!value) {
                return this.failBeforeIo(
                    intent,
                    new InariAgentError(
                        "drawer_intent_missing",
                        "The Agent did not retain the Drawer Intent.",
                    ),
                );
            }
            result = serverResult(intent, value);
        }
        return this.settle(intent, result);
    }

    async reconcileUncertain(client, intent, error) {
        try {
            const page = await client.queryDrawerIntents([intent.drawer_intent_id]);
            const value = page.intents?.[0];
            if (value) {
                return this.settle(intent, serverResult(intent, value));
            }
            return this.failBeforeIo(intent, error);
        } catch (reconciliationError) {
            this.retryableIntent = null;
            this.uncertainIntent = intent;
            await this.remember("outcome_unknown", intent);
            return controlledResult(intent, {
                state: "outcome_unknown",
                error_code: "drawer_outcome_unknown",
                error: reconciliationError,
            });
        }
    }

    async settle(intent, result) {
        if (result.state === "failed" && result.retryable) {
            this.retryableIntent = intent;
            await this.remember("retryable", intent);
        } else {
            this.retryableIntent = null;
        }
        if (result.state === "outcome_unknown" || result.state === "in_progress") {
            this.uncertainIntent = intent;
            await this.remember("outcome_unknown", intent);
            return controlledResult(intent, {
                ...result,
                state: "outcome_unknown",
                retryable: false,
            });
        }
        if (result.state === "accepted") {
            this.retryableIntent = intent;
            await this.remember("retryable", intent);
            return controlledResult(intent, {
                ...result,
                state: "failed",
                retryable: true,
                error_code: "drawer_not_started",
            });
        }
        this.uncertainIntent = null;
        await this.clearRemembered();
        return result;
    }

    async failBeforeIo(intent, error) {
        this.retryableIntent = intent;
        if (!(await this.remember("retryable", intent))) {
            return this.journalUnavailable(intent, error);
        }
        return controlledResult(intent, {
            state: "failed",
            retryable: true,
            error_code: error?.code || "drawer_unavailable",
            error,
        });
    }

    async remember(state, intent) {
        if (!this.scopeKey || !this.journalAvailable) {
            return false;
        }
        try {
            await this.store.put({ scope_key: this.scopeKey, state, intent });
            return true;
        } catch {
            this.journalAvailable = false;
            return false;
        }
    }

    journalUnavailable(intent, error = null) {
        this.retryableIntent = null;
        return controlledResult(intent, {
            state: "failed",
            retryable: false,
            error_code: "drawer_journal_unavailable",
            error,
        });
    }

    async clearRemembered() {
        if (!this.scopeKey || !this.journalAvailable) {
            return;
        }
        try {
            await this.store.delete(this.scopeKey);
        } catch {
            this.journalAvailable = false;
        }
    }
}
