/** @odoo-module */

const CONTEXT_FIELDS = [
    "binding_revision_id",
    "contract_major",
    "copy_ordinal",
    "device_id",
    "origin",
    "origin_submission_key",
    "print_intent_id",
];
const POS_ORIGIN_FIELDS = [
    "content_revision",
    "document_kind",
    "kind",
    "offline_order_id",
    "pos_session_id",
    "server_order_id",
];
const PREPARATION_ORIGIN_FIELDS = [
    ...POS_ORIGIN_FIELDS,
    "preparation_revision",
    "segment_index",
    "segment_kind",
].toSorted();
const PREPARATION_SEGMENTS = new Set(["new", "cancelled", "note_update", "notes"]);

function freezeValue(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) {
        return value;
    }
    for (const child of Object.values(value)) {
        freezeValue(child);
    }
    return Object.freeze(value);
}

function requireString(value, name) {
    if (typeof value !== "string" || value.trim() === "") {
        throw new TypeError(`${name} must be a non-empty string`);
    }
    return value;
}

function requireExactFields(value, expected, name) {
    const fields = Object.keys(value).toSorted();
    if (
        fields.length !== expected.length ||
        fields.some((field, index) => field !== expected[index])
    ) {
        throw new TypeError(`${name} contains an unsupported field`);
    }
}

function serverOrderId(order) {
    const candidate = order.server_id ?? (Number.isInteger(order.id) ? order.id : null);
    return candidate == null ? null : String(candidate);
}

function randomIntentId(randomUUID) {
    return `pi_v1_${randomUUID().replaceAll("-", "")}`;
}

async function sha256Hex(blob, subtle) {
    const digest = await subtle.digest("SHA-256", await blob.arrayBuffer());
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Capture all mutable POS values before Odoo renders the receipt. */
export function createReceiptPlan({
    binding,
    order,
    posSessionId,
    randomUUID = () => crypto.randomUUID(),
}) {
    return createLocalPrintPlan({
        binding,
        order,
        posSessionId,
        documentKind: "customer_receipt",
        copyOrdinal: Number(order?.nb_print || 0) + 1,
        randomUUID,
    });
}

/** Capture one local print copy before Odoo renders its document. */
export function createLocalPrintPlan({
    binding,
    order,
    posSessionId,
    documentKind,
    copyOrdinal,
    originKind = "pos",
    preparationRevision = null,
    segmentIndex = null,
    segmentKind = null,
    randomUUID = () => crypto.randomUUID(),
}) {
    if (!binding || !order || typeof randomUUID !== "function") {
        throw new TypeError("print planning requires a binding, order, and random source");
    }
    if (!Number.isInteger(copyOrdinal) || copyOrdinal < 1) {
        throw new TypeError("print copy ordinal must be a positive integer");
    }
    if (!new Set(["pos", "preparation"]).has(originKind)) {
        throw new TypeError("print origin kind is invalid");
    }
    if (
        originKind === "preparation" &&
        (!Number.isInteger(segmentIndex) ||
            segmentIndex < 0 ||
            segmentIndex > 63 ||
            !preparationRevision ||
            !PREPARATION_SEGMENTS.has(segmentKind))
    ) {
        throw new TypeError("preparation planning requires its segment and revision");
    }
    const plan = {
        print_intent_id: randomIntentId(randomUUID),
        pos_session_id: requireString(String(posSessionId), "pos_session_id"),
        offline_order_id: requireString(String(order.uuid), "offline_order_id"),
        server_order_id: serverOrderId(order),
        document_kind: requireString(documentKind, "document_kind"),
        binding_revision_id: requireString(
            String(binding.binding_revision_id),
            "binding_revision_id",
        ),
        device_id: requireString(String(binding.device_id), "device_id"),
        copy_ordinal: copyOrdinal,
        origin_kind: originKind,
    };
    if (originKind === "preparation") {
        Object.assign(plan, {
            preparation_revision: requireString(preparationRevision, "preparation_revision"),
            segment_index: segmentIndex,
            segment_kind: requireString(segmentKind, "segment_kind"),
        });
    }
    return freezeValue(plan);
}

/** Bind the physical JPEG revision to the immutable plan. */
export async function materializeSubmissionContext(plan, jpeg, { subtle = crypto.subtle } = {}) {
    if (!Object.isFrozen(plan) || !(jpeg instanceof Blob) || jpeg.type !== "image/jpeg") {
        throw new TypeError("context materialization requires a frozen plan and JPEG Blob");
    }
    const contentRevision = await sha256Hex(jpeg, subtle);
    return createSubmissionContext({
        contract_major: 1,
        print_intent_id: plan.print_intent_id,
        origin_submission_key: [
            plan.pos_session_id,
            plan.offline_order_id,
            plan.document_kind,
            plan.binding_revision_id,
            ...(plan.origin_kind === "preparation"
                ? [plan.preparation_revision, plan.segment_index, plan.segment_kind]
                : []),
            plan.copy_ordinal,
        ].join(":"),
        origin: {
            kind: plan.origin_kind,
            pos_session_id: plan.pos_session_id,
            offline_order_id: plan.offline_order_id,
            server_order_id: plan.server_order_id,
            document_kind: plan.document_kind,
            content_revision: contentRevision,
            ...(plan.origin_kind === "preparation"
                ? {
                      preparation_revision: plan.preparation_revision,
                      segment_index: plan.segment_index,
                      segment_kind: plan.segment_kind,
                  }
                : {}),
        },
        binding_revision_id: plan.binding_revision_id,
        device_id: plan.device_id,
        copy_ordinal: plan.copy_ordinal,
    });
}

/** Build the exact context accepted by the local Agent contract. */
export function createSubmissionContext(input) {
    if (!input || typeof input !== "object" || !input.origin || typeof input.origin !== "object") {
        throw new TypeError("submission context and origin must be objects");
    }
    requireExactFields(input, CONTEXT_FIELDS, "submission context");
    if (!new Set(["pos", "preparation"]).has(input.origin.kind)) {
        throw new TypeError("submission origin kind is invalid");
    }
    if (
        input.origin.kind === "preparation" &&
        (!Number.isInteger(input.origin.segment_index) ||
            input.origin.segment_index < 0 ||
            input.origin.segment_index > 63 ||
            !PREPARATION_SEGMENTS.has(input.origin.segment_kind))
    ) {
        throw new TypeError("preparation segment identity is invalid");
    }
    const originFields =
        input.origin.kind === "preparation" ? PREPARATION_ORIGIN_FIELDS : POS_ORIGIN_FIELDS;
    requireExactFields(input.origin, originFields, "submission origin");
    if (input.contract_major !== 1) {
        throw new TypeError("submission context requires Contract Major 1");
    }
    if (!Number.isInteger(input.copy_ordinal) || input.copy_ordinal < 1) {
        throw new TypeError("copy_ordinal must be a positive integer");
    }
    return freezeValue({
        contract_major: 1,
        print_intent_id: requireString(input.print_intent_id, "print_intent_id"),
        origin_submission_key: requireString(input.origin_submission_key, "origin_submission_key"),
        origin: {
            kind: input.origin.kind,
            pos_session_id: requireString(input.origin.pos_session_id, "origin.pos_session_id"),
            offline_order_id: requireString(
                input.origin.offline_order_id,
                "origin.offline_order_id",
            ),
            server_order_id:
                input.origin.server_order_id == null ? null : String(input.origin.server_order_id),
            document_kind: requireString(input.origin.document_kind, "origin.document_kind"),
            content_revision: requireString(
                input.origin.content_revision,
                "origin.content_revision",
            ),
            ...(input.origin.kind === "preparation"
                ? {
                      preparation_revision: requireString(
                          input.origin.preparation_revision,
                          "origin.preparation_revision",
                      ),
                      segment_index: input.origin.segment_index,
                      segment_kind: requireString(input.origin.segment_kind, "origin.segment_kind"),
                  }
                : {}),
        },
        binding_revision_id: requireString(input.binding_revision_id, "binding_revision_id"),
        device_id: requireString(input.device_id, "device_id"),
        copy_ordinal: input.copy_ordinal,
    });
}

export function contextKey(context) {
    return context.print_intent_id;
}

export function envelopeFor(context) {
    return {
        contract_major: context.contract_major,
        operation: "receipt_image",
        media_type: "image/jpeg",
        context,
    };
}
