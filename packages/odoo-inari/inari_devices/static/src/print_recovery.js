/** @odoo-module */

import { InariAgentError } from "./agent_client";
import { createSubmissionContext } from "./submission_context";

const AGENT_STATES = new Set([
    "accepted",
    "in_progress",
    "output_confirmed",
    "failed",
    "outcome_unknown",
    "expired",
    "canceled",
]);
const ADVANCED_STATES = new Set([
    "accepted",
    "in_progress",
    "output_confirmed",
    "outcome_unknown",
    "resolved",
]);
const ACTIONS = new Set(["retry", "reconcile", "browser_print", "finish_without_ticket"]);
const LOCALLY_RECOVERABLE_FAILURES = new Set(["agent_endpoint_required", "pairing_required"]);
const WATCHED_STATES = new Set(["pending_agent", "accepted", "in_progress"]);

function safeCode(error, fallback) {
    return typeof error?.code === "string" && error.code ? error.code : fallback;
}

function uncertainSubmission(error) {
    if (!(error instanceof InariAgentError)) {
        return true;
    }
    return (
        error.status === null &&
        !new Set(["agent_endpoint_required", "pairing_required"]).has(error.code)
    );
}

function descriptorFor(context, descriptor = {}) {
    return Object.freeze({
        order_reference:
            typeof descriptor.order_reference === "string" && descriptor.order_reference
                ? descriptor.order_reference
                : context.origin.offline_order_id,
        printer_name:
            typeof descriptor.printer_name === "string" && descriptor.printer_name
                ? descriptor.printer_name
                : context.device_id,
        agent_channel: descriptor.agent_channel ? Object.freeze({ ...descriptor.agent_channel }) : null,
    });
}

function assertAgentPage(value, requestedIds) {
    if (
        !value ||
        !Array.isArray(value.jobs) ||
        !Array.isArray(value.missing_print_intent_ids) ||
        !Number.isInteger(value.high_water_mark) ||
        value.high_water_mark < 0
    ) {
        throw new TypeError("The Agent returned an invalid Print Job page");
    }
    const requested = new Set(requestedIds);
    const seen = new Set();
    for (const job of value.jobs) {
        if (
            !job ||
            typeof job.print_intent_id !== "string" ||
            !requested.has(job.print_intent_id) ||
            seen.has(job.print_intent_id) ||
            typeof job.print_job_id !== "string" ||
            !AGENT_STATES.has(job.state) ||
            !Number.isInteger(job.state_version) ||
            job.state_version < 1
        ) {
            throw new TypeError("The Agent returned an invalid Print Job snapshot");
        }
        seen.add(job.print_intent_id);
    }
    for (const printIntentId of value.missing_print_intent_ids) {
        if (
            typeof printIntentId !== "string" ||
            !requested.has(printIntentId) ||
            seen.has(printIntentId)
        ) {
            throw new TypeError("The Agent returned an invalid missing Print Intent");
        }
        seen.add(printIntentId);
    }
    if (seen.size !== requested.size) {
        throw new TypeError("The Agent omitted a requested Print Intent");
    }
    return value;
}

function assertAccepted(result, context) {
    if (
        !result ||
        result.state !== "accepted" ||
        result.print_intent_id !== context.print_intent_id ||
        result.device_id !== context.device_id ||
        typeof result.print_job_id !== "string" ||
        !result.print_job_id ||
        !Number.isInteger(result.state_version) ||
        result.state_version < 1
    ) {
        throw new TypeError("The Agent returned an invalid admission result");
    }
    return result;
}

function persisted(entry) {
    return {
        key: entry.key,
        context: entry.context,
        descriptor: entry.descriptor,
        state: entry.state,
        attention: entry.attention,
        print_job_id: entry.printJobId,
        state_version: entry.stateVersion,
        retryable: entry.retryable,
        failure_code: entry.failureCode,
        confirmation_evidence: entry.confirmationEvidence,
        resolution: entry.resolution,
        updated_at: new Date().toISOString(),
    };
}

function entryFromRecord(record) {
    if (!record || record.key !== record.context?.print_intent_id) {
        throw new TypeError("The stored recovery task has an invalid identity");
    }
    const context = createSubmissionContext(record.context);
    if (
        typeof record.state !== "string" ||
        !new Set([
            "submission_pending",
            "pending_agent",
            "accepted",
            "in_progress",
            "output_confirmed",
            "failed",
            "outcome_unknown",
            "expired",
            "canceled",
            "content_unavailable",
            "resolved",
        ]).has(record.state)
    ) {
        throw new TypeError("The stored recovery task has an invalid state");
    }
    return {
        key: context.print_intent_id,
        context,
        descriptor: descriptorFor(context, record.descriptor),
        jpeg: null,
        client: null,
        state: record.state,
        attention: record.state !== "resolved" && record.state !== "output_confirmed",
        printJobId: typeof record.print_job_id === "string" ? record.print_job_id : null,
        stateVersion: Number.isInteger(record.state_version) ? record.state_version : null,
        retryable: record.retryable === true,
        failureCode: typeof record.failure_code === "string" ? record.failure_code : null,
        confirmationEvidence:
            typeof record.confirmation_evidence === "string" ? record.confirmation_evidence : null,
        resolution: typeof record.resolution === "string" ? record.resolution : null,
        error: null,
        operation: null,
    };
}

function defaultBrowserPrint(jpeg) {
    const url = URL.createObjectURL(jpeg);
    const frame = document.createElement("iframe");
    frame.title = "Receipt prepared for browser print";
    frame.style.position = "fixed";
    frame.style.width = "0";
    frame.style.height = "0";
    frame.style.border = "0";
    frame.src = url;
    return new Promise((resolve, reject) => {
        const remove = () => {
            frame.remove();
            URL.revokeObjectURL(url);
        };
        frame.addEventListener(
            "load",
            () => {
                try {
                    frame.contentWindow.focus();
                    frame.contentWindow.print();
                    globalThis.setTimeout(remove, 60_000);
                    resolve();
                } catch (error) {
                    remove();
                    reject(error);
                }
            },
            { once: true },
        );
        frame.addEventListener(
            "error",
            () => {
                remove();
                reject(new Error("The browser could not prepare the receipt print dialog"));
            },
            { once: true },
        );
        document.body.append(frame);
    });
}

function admitted(entry) {
    return Boolean(entry.printJobId) || entry.state === "resolved";
}

/** Own local recovery identity, persistence, admission, and reconciliation. */
export class PrintRecoveryCoordinator {
    constructor({
        store,
        clientForContext,
        onPreparationSettled = async () => {},
        browserPrint = defaultBrowserPrint,
        schedule = (callback, delay) => globalThis.setTimeout(callback, delay),
        cancelSchedule = (token) => globalThis.clearTimeout(token),
        watchIntervalMs = 2_000,
    } = {}) {
        if (
            !store ||
            typeof clientForContext !== "function" ||
            typeof schedule !== "function" ||
            typeof cancelSchedule !== "function" ||
            !Number.isInteger(watchIntervalMs) ||
            watchIntervalMs < 1
        ) {
            throw new TypeError("Print recovery requires a store, Agent resolver, and scheduler");
        }
        this.store = store;
        this.clientForContext = clientForContext;
        this.onPreparationSettled = onPreparationSettled;
        this.browserPrint = browserPrint;
        this.schedule = schedule;
        this.cancelSchedule = cancelSchedule;
        this.watchIntervalMs = watchIntervalMs;
        this.entries = new Map();
        this.listeners = new Set();
        this.lanes = new Map();
        this.restorePromise = null;
        this.watching = false;
        this.watchTimer = null;
    }

    subscribe(listener) {
        if (typeof listener !== "function") {
            throw new TypeError("A recovery subscriber must be a function");
        }
        this.listeners.add(listener);
        listener(this.snapshot());
        return () => this.listeners.delete(listener);
    }

    notify() {
        const snapshot = this.snapshot();
        for (const listener of this.listeners) {
            listener(snapshot);
        }
    }

    startWatching() {
        this.watching = true;
        this.scheduleWatch();
    }

    stopWatching() {
        this.watching = false;
        if (this.watchTimer !== null) {
            this.cancelSchedule(this.watchTimer);
            this.watchTimer = null;
        }
    }

    scheduleWatch() {
        if (
            !this.watching ||
            this.watchTimer !== null ||
            ![...this.entries.values()].some(
                (entry) => !entry.operation && WATCHED_STATES.has(entry.state),
            )
        ) {
            return;
        }
        this.watchTimer = this.schedule(() => this.watch(), this.watchIntervalMs);
    }

    async watch() {
        this.watchTimer = null;
        const keys = [...this.entries.values()]
            .filter((entry) => !entry.operation && WATCHED_STATES.has(entry.state))
            .map((entry) => entry.key);
        try {
            if (keys.length) {
                await this.reconcile(keys, { interactive: false });
            }
        } catch {
            // A later poll can recover after a transient local persistence failure.
        } finally {
            this.scheduleWatch();
        }
    }

    async restore() {
        if (!this.restorePromise) {
            this.restorePromise = this.restoreRecords();
        }
        return this.restorePromise;
    }

    async restoreRecords() {
        const records = await this.store.list();
        for (const record of records) {
            let entry;
            try {
                entry = entryFromRecord(record);
            } catch {
                // An invalid record cannot become Device Work or an operator action.
                continue;
            }
            if (entry.state === "resolved" || entry.state === "output_confirmed") {
                // oxlint-disable-next-line no-await-in-loop
                await this.store.settle(record);
            } else {
                this.entries.set(entry.key, entry);
            }
        }
        this.notify();
        return this.snapshot();
    }

    async knownResult(key) {
        const entry = this.entries.get(key);
        if (entry) return this.result(entry);
        const record = await this.store.getSettled(key);
        return record ? this.result(entryFromRecord(record)) : null;
    }

    async receiptContext(plan) {
        const matches = [...this.entries.values()].filter(
            ({ context }) =>
                context.origin.kind === "pos" &&
                context.origin.document_kind === "customer_receipt" &&
                context.origin.offline_order_id === plan.offline_order_id &&
                context.origin.pos_session_id === plan.pos_session_id &&
                context.binding_revision_id === plan.binding_revision_id &&
                context.device_id === plan.device_id &&
                context.copy_ordinal === plan.copy_ordinal,
        );
        if (matches.length > 1) {
            throw new TypeError("This receipt copy has conflicting recovery identities");
        }
        return matches[0]?.context || await this.store.receiptContext(plan);
    }

    receiptContext(plan) {
        const matches = [...this.entries.values()].filter(
            ({ context }) =>
                context.origin.kind === "pos" &&
                context.origin.document_kind === "customer_receipt" &&
                context.origin.offline_order_id === plan.offline_order_id &&
                context.origin.pos_session_id === plan.pos_session_id &&
                context.binding_revision_id === plan.binding_revision_id &&
                context.device_id === plan.device_id &&
                context.copy_ordinal === plan.copy_ordinal,
        );
        if (matches.length > 1) {
            throw new TypeError("This receipt copy has conflicting recovery identities");
        }
        return matches[0]?.context || null;
    }

    async enqueue({ context, jpeg, client, descriptor }) {
        if (!Object.isFrozen(context) || !(jpeg instanceof Blob) || jpeg.type !== "image/jpeg") {
            throw new TypeError("Recovery admission requires an immutable context and JPEG Blob");
        }
        const settled = await this.store.getSettled(context.print_intent_id);
        if (settled) return this.result(entryFromRecord(settled));
        let entry = this.entries.get(context.print_intent_id);
        if (entry) {
            if (entry.context.origin.content_revision !== context.origin.content_revision) {
                throw new TypeError("A Print Intent cannot change its content revision");
            }
            entry.jpeg = jpeg;
            entry.client = client;
            entry.descriptor = descriptorFor(context, descriptor);
            if (entry.operation) {
                await entry.operation;
            } else if (!ADVANCED_STATES.has(entry.state)) {
                await this.retryEntry(entry);
            }
            return this.result(entry);
        }
        entry = {
            key: context.print_intent_id,
            context,
            descriptor: descriptorFor(context, descriptor),
            jpeg,
            client,
            state: "submission_pending",
            attention: false,
            printJobId: null,
            stateVersion: null,
            retryable: true,
            failureCode: null,
            confirmationEvidence: null,
            resolution: null,
            error: null,
            operation: null,
        };
        this.entries.set(entry.key, entry);
        await this.submitInLane(entry);
        return this.result(entry);
    }

    async submitInLane(entry) {
        const laneKey = `${entry.context.binding_revision_id}|${entry.context.device_id}`;
        const previous = this.lanes.get(laneKey) || Promise.resolve();
        const operation = previous.catch(() => {}).then(() => this.submitEntry(entry));
        this.lanes.set(laneKey, operation);
        entry.operation = operation;
        try {
            await operation;
        } finally {
            entry.operation = null;
            if (this.lanes.get(laneKey) === operation) {
                this.lanes.delete(laneKey);
            }
            this.scheduleWatch();
        }
    }

    async submitEntry(entry) {
        entry.state = "pending_agent";
        entry.failureCode = null;
        entry.error = null;
        await this.save(entry);
        try {
            if (!entry.client) {
                entry.client = await this.clientForContext(entry.context, {
                    interactive: true,
                    channel: entry.descriptor.agent_channel,
                });
            }
            if (!entry.client) {
                throw new InariAgentError(
                    "pairing_required",
                    "Pair this browser with the Inari Agent before printing.",
                );
            }
            const result = assertAccepted(
                await entry.client.submit(entry.context, entry.jpeg),
                entry.context,
            );
            entry.state = "accepted";
            entry.attention = false;
            entry.printJobId = result.print_job_id;
            entry.stateVersion = result.state_version;
            entry.retryable = false;
            await this.save(entry);
            await this.settlePreparation(entry);
        } catch (error) {
            entry.error = error;
            entry.failureCode = safeCode(error, "submission_failed");
            entry.retryable =
                error?.retryable === true || LOCALLY_RECOVERABLE_FAILURES.has(entry.failureCode);
            entry.state = uncertainSubmission(error) ? "pending_agent" : "failed";
            entry.attention = true;
            await this.save(entry);
        }
    }

    async reconcile(keys, { interactive = true } = {}) {
        const selected = (
            keys ? keys.map((key) => this.entries.get(key)) : [...this.entries.values()]
        )
            .filter(Boolean)
            .filter((entry) => entry.state !== "resolved" && entry.state !== "output_confirmed");
        const groups = new Map();
        for (const entry of selected) {
            let client = entry.client;
            if (!client) {
                try {
                    // oxlint-disable-next-line no-await-in-loop
                    client = await this.clientForContext(entry.context, {
                        interactive, channel: entry.descriptor.agent_channel,
                    });
                } catch (error) {
                    entry.error = error;
                }
            }
            if (!client) {
                entry.attention = true;
                entry.failureCode = safeCode(entry.error, "pairing_required");
                entry.retryable = LOCALLY_RECOVERABLE_FAILURES.has(entry.failureCode);
                // oxlint-disable-next-line no-await-in-loop
                await this.save(entry);
                continue;
            }
            entry.client = client;
            const group = groups.get(client) || [];
            group.push(entry);
            groups.set(client, group);
        }
        for (const [client, entries] of groups) {
            for (let offset = 0; offset < entries.length; offset += 100) {
                const batch = entries.slice(offset, offset + 100);
                // oxlint-disable-next-line no-await-in-loop
                await this.reconcileBatch(client, batch);
            }
        }
        this.notify();
        return this.snapshot();
    }

    async reconcileBatch(client, entries) {
        const ids = entries.map((entry) => entry.key);
        try {
            const page = assertAgentPage(await client.queryPrintJobs(ids), ids);
            const jobs = new Map(page.jobs.map((job) => [job.print_intent_id, job]));
            for (const entry of entries) {
                const job = jobs.get(entry.key);
                if (job) {
                    this.applyJob(entry, job);
                } else if (entry.printJobId) {
                    entry.failureCode = "job_not_visible";
                    entry.retryable = false;
                    entry.attention = true;
                } else {
                    entry.state = entry.jpeg ? "failed" : "content_unavailable";
                    entry.retryable = Boolean(entry.jpeg);
                    entry.failureCode = entry.jpeg ? "not_accepted" : "content_unavailable";
                    entry.attention = true;
                }
                // oxlint-disable-next-line no-await-in-loop
                await this.save(entry);
                if (admitted(entry)) {
                    // oxlint-disable-next-line no-await-in-loop
                    await this.settlePreparation(entry);
                }
            }
        } catch (error) {
            for (const entry of entries) {
                entry.error = error;
                entry.failureCode = safeCode(error, "reconciliation_failed");
                entry.attention = true;
                // oxlint-disable-next-line no-await-in-loop
                await this.save(entry);
            }
        }
    }

    applyJob(entry, job) {
        if (entry.printJobId && entry.printJobId !== job.print_job_id) {
            throw new TypeError("A Print Intent resolved to a different Print Job");
        }
        if (entry.stateVersion && job.state_version < entry.stateVersion) {
            return;
        }
        entry.printJobId = job.print_job_id;
        entry.stateVersion = job.state_version;
        entry.state = job.state;
        entry.retryable = false;
        entry.failureCode = job.error_code || null;
        entry.confirmationEvidence = job.confirmation_evidence || null;
        entry.attention = !new Set(["accepted", "output_confirmed"]).has(job.state);
    }

    async act(key, action) {
        if (!ACTIONS.has(action)) {
            throw new TypeError("The recovery action is not supported");
        }
        const entry = this.entries.get(key);
        if (!entry) {
            throw new Error("The recovery task is no longer available");
        }
        if (!this.actions(entry).includes(action)) {
            throw new Error("The recovery action is not available in this state");
        }
        if (action === "retry") {
            await this.retryEntry(entry);
        } else if (action === "reconcile") {
            await this.reconcile([key]);
        } else if (action === "browser_print") {
            await this.browserPrint(entry.jpeg);
            await this.resolve(entry, "browser_print");
        } else {
            await this.resolve(entry, "finish_without_ticket");
        }
        return this.snapshot();
    }

    async retryEntry(entry) {
        if (!entry.jpeg) {
            throw new Error("The exact print content is not available in this tab");
        }
        if (!entry.client) {
            entry.client = await this.clientForContext(entry.context, {
                interactive: true, channel: entry.descriptor.agent_channel,
            });
        }
        if (!entry.client) {
            throw new Error("Pair this browser before you retry the print");
        }
        entry.state = "submission_pending";
        entry.attention = true;
        await this.save(entry);
        await this.submitInLane(entry);
    }

    async resolve(entry, resolution) {
        entry.state = "resolved";
        entry.resolution = resolution;
        entry.attention = false;
        entry.retryable = false;
        await this.save(entry);
        await this.settlePreparation(entry);
    }

    async settlePreparation(entry) {
        if (entry.context.origin.kind === "preparation") {
            await this.onPreparationSettled({
                print_intent_id: entry.key,
                offline_order_id: entry.context.origin.offline_order_id,
                state: entry.state,
                resolution: entry.resolution,
            });
        }
    }

    blocksPreparation(offlineOrderId) {
        return [...this.entries.values()].some(
            (entry) =>
                entry.context.origin.kind === "preparation" &&
                entry.context.origin.offline_order_id === offlineOrderId &&
                !admitted(entry),
        );
    }

    blockingPreparationOrderIds() {
        return new Set(
            [...this.entries.values()]
                .filter((entry) => entry.context.origin.kind === "preparation" && !admitted(entry))
                .map((entry) => entry.context.origin.offline_order_id),
        );
    }

    actions(entry) {
        const actions = [];
        if (entry.state === "failed" && entry.retryable && entry.jpeg) {
            actions.push("retry");
        }
        if (
            new Set([
                "submission_pending",
                "pending_agent",
                "accepted",
                "in_progress",
                "outcome_unknown",
            ]).has(entry.state)
        ) {
            actions.push("reconcile");
        }
        if (entry.context.origin.kind === "pos" && entry.state === "failed" && entry.jpeg) {
            actions.push("browser_print");
        }
        if (!new Set(["output_confirmed", "resolved"]).has(entry.state)) {
            actions.push("finish_without_ticket");
        }
        return actions;
    }

    snapshot() {
        return [...this.entries.values()]
            .filter(
                (entry) =>
                    entry.attention && !new Set(["output_confirmed", "resolved"]).has(entry.state),
            )
            .map((entry) =>
                Object.freeze({
                    key: entry.key,
                    print_intent_id: entry.key,
                    print_job_id: entry.printJobId,
                    origin_kind: entry.context.origin.kind,
                    document_kind: entry.context.origin.document_kind,
                    order_reference: entry.descriptor.order_reference,
                    printer_name: entry.descriptor.printer_name,
                    state: entry.state,
                    state_version: entry.stateVersion,
                    failure_code: entry.failureCode,
                    confirmation_evidence: entry.confirmationEvidence,
                    content_available: Boolean(entry.jpeg),
                    actions: Object.freeze(this.actions(entry)),
                }),
            );
    }

    result(entry) {
        return {
            accepted: Boolean(entry.printJobId),
            state: entry.state,
            error: entry.error,
            context: entry.context,
            print_job_id: entry.printJobId,
        };
    }

    async save(entry) {
        if (entry.state === "resolved" || entry.state === "output_confirmed") {
            await this.store.settle(persisted(entry));
            this.entries.delete(entry.key);
            entry.jpeg = null;
            entry.client = null;
        } else {
            await this.store.put(persisted(entry));
        }
        this.notify();
        this.scheduleWatch();
    }
}
