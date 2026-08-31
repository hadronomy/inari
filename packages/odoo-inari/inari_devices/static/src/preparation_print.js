/** @odoo-module */

import { createLocalPrintPlan } from "./submission_context";

const PREPARATION_SOURCE = Symbol("inari_preparation_source");
const SEGMENTS = ["new", "cancelled", "note_update", "notes"];

function sourceKey(binding, source) {
    return `${binding.binding_revision_id}|${source.segment}`;
}

function preparationSegments(changes, orderChange) {
    const segments = [];
    if (changes.new.length) {
        segments.push("new");
    }
    if (changes.cancelled.length) {
        segments.push("cancelled");
    }
    if (changes.noteUpdate.length) {
        segments.push("note_update");
    }
    if (orderChange.internal_note || orderChange.general_customer_note) {
        segments.push("notes");
    }
    return segments;
}

async function preparationRevision(orderChange) {
    const encoded = new TextEncoder().encode(
        JSON.stringify({
            cancelled: orderChange.cancelled,
            general_customer_note: orderChange.general_customer_note || "",
            internal_note: orderChange.internal_note || "",
            new: orderChange.new,
            noteUpdate: orderChange.noteUpdate,
        }),
    );
    const digest = await crypto.subtle.digest("SHA-256", encoded);
    return `sha256:${[...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("")}`;
}

export function markPreparationSource(orderData, { order, orderChange, reprint }) {
    if (!orderData || !order?.uuid || !orderChange || typeof orderChange !== "object") {
        throw new TypeError("preparation source requires an order and its change set");
    }
    Object.defineProperty(orderData, PREPARATION_SOURCE, {
        configurable: false,
        enumerable: true,
        writable: false,
        value: Object.freeze({ order, orderChange, reprint: Boolean(reprint), segment: null }),
    });
    return orderData;
}

export function markPreparationSegments(receiptsData, orderData, changes, orderChange) {
    const source = orderData?.[PREPARATION_SOURCE];
    if (!source) {
        return receiptsData;
    }
    const segments = preparationSegments(changes, orderChange);
    if (segments.length !== receiptsData.length) {
        throw new TypeError("Odoo returned an invalid preparation receipt set");
    }
    receiptsData.forEach((data, index) => {
        if (!SEGMENTS.includes(segments[index])) {
            throw new TypeError("Odoo returned an invalid preparation segment");
        }
        Object.defineProperty(data, PREPARATION_SOURCE, {
            configurable: false,
            enumerable: false,
            writable: false,
            value: Object.freeze({ ...source, segment: segments[index], segmentIndex: index }),
        });
    });
    return receiptsData;
}

export function preparationSource(data) {
    return data?.[PREPARATION_SOURCE] || null;
}

/** Keep one physical-copy identity across Odoo's preparation retry callback. */
export class PreparationPlanBook {
    constructor({ randomUUID = () => crypto.randomUUID() } = {}) {
        this.randomUUID = randomUUID;
        this.entries = new WeakMap();
    }

    async plan({ binding, source, posSessionId }) {
        if (!binding?.binding_revision_id || !binding?.device_id || !source?.segment) {
            throw new TypeError("preparation planning requires a Binding and receipt source");
        }
        let plans = this.entries.get(source.orderChange);
        if (!plans) {
            plans = new Map();
            this.entries.set(source.orderChange, plans);
        }
        const key = sourceKey(binding, source);
        const previous = plans.get(key);
        if (previous && previous.state !== "accepted") {
            return previous.plan;
        }
        const copyOrdinal = previous ? previous.plan.copy_ordinal + 1 : source.reprint ? 2 : 1;
        const plan = createLocalPrintPlan({
            binding,
            order: source.order,
            posSessionId,
            documentKind: "preparation_ticket",
            copyOrdinal,
            originKind: "preparation",
            preparationRevision: await preparationRevision(source.orderChange),
            segmentIndex: source.segmentIndex,
            segmentKind: source.segment,
            randomUUID: this.randomUUID,
        });
        plans.set(key, { plan, state: "planned" });
        return plan;
    }

    settle({ binding, source, result }) {
        const entry = this.entries.get(source.orderChange)?.get(sourceKey(binding, source));
        if (entry) {
            entry.state = result?.accepted ? "accepted" : "failed";
        }
    }
}
