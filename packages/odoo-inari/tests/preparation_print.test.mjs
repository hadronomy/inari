import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { MemoryContextStore } from "../inari_devices/static/src/context_store.js";
import { InariReceiptPrinter } from "../inari_devices/static/src/inari_printer.js";
import {
    markPreparationSegments,
    markPreparationSource,
    PreparationPlanBook,
    preparationSource,
} from "../inari_devices/static/src/preparation_print.js";

function binding() {
    return {
        binding_revision_id: "binding-kitchen-1",
        device_id: "printer-kitchen-1",
    };
}

function source() {
    const order = { uuid: "order-1", id: 17 };
    const orderChange = {
        new: [{ product_id: 1 }],
        cancelled: [{ product_id: 2 }],
        noteUpdate: [],
        internal_note: "",
        general_customer_note: "",
    };
    const orderData = {};
    markPreparationSource(orderData, { order, orderChange, reprint: false });
    const receipts = markPreparationSegments(
        [{ ...orderData }, { ...orderData }],
        orderData,
        orderChange,
        orderChange,
    );
    return { orderChange, receipts };
}

describe("Odoo preparation printing", () => {
    test("keeps Odoo segment order and reuses a failed physical-copy identity", async () => {
        let sequence = 0;
        const plans = new PreparationPlanBook({
            randomUUID: () => `00000000-0000-0000-0000-${String(++sequence).padStart(12, "0")}`,
        });
        const { receipts } = source();
        const firstSource = preparationSource(receipts[0]);
        const secondSource = preparationSource(receipts[1]);

        assert.equal(firstSource.segment, "new");
        assert.equal(secondSource.segment, "cancelled");

        const first = await plans.plan({
            binding: binding(),
            source: firstSource,
            posSessionId: 42,
        });
        plans.settle({ binding: binding(), source: firstSource, result: { accepted: false } });
        const retry = await plans.plan({
            binding: binding(),
            source: firstSource,
            posSessionId: 42,
        });
        plans.settle({ binding: binding(), source: firstSource, result: { accepted: true } });
        const reprint = await plans.plan({
            binding: binding(),
            source: firstSource,
            posSessionId: 42,
        });

        assert.equal(first, retry);
        assert.notEqual(first.print_intent_id, reprint.print_intent_id);
        assert.equal(first.copy_ordinal, 1);
        assert.equal(reprint.copy_ordinal, 2);
        assert.equal(first.document_kind, "preparation_ticket");
    });

    test("retries the cached JPEG and immutable context without rendering again", async () => {
        let renders = 0;
        const contexts = [];
        const printer = new InariReceiptPrinter({
            client: {
                async submit(context) {
                    contexts.push(context);
                    if (contexts.length === 1) {
                        throw new Error("offline");
                    }
                    return { state: "accepted", print_job_id: "job-1" };
                },
            },
            contextStore: new MemoryContextStore(),
            render: async () => {
                renders += 1;
                return new Blob(["jpeg"], { type: "image/jpeg" });
            },
        });
        const { receipts } = source();
        const plan = await new PreparationPlanBook({
            randomUUID: () => "00000000-0000-0000-0000-000000000001",
        }).plan({
            binding: binding(),
            source: preparationSource(receipts[0]),
            posSessionId: 42,
        });

        const failed = await printer.printReceipt({}, plan);
        const accepted = await printer.printReceipt({}, plan);

        assert.equal(failed.accepted, false);
        assert.equal(accepted.accepted, true);
        assert.equal(renders, 1);
        assert.equal(contexts.length, 2);
        assert.equal(contexts[0], contexts[1]);
        assert.match(contexts[0].origin_submission_key, /binding-kitchen-1/);
        assert.match(contexts[0].origin_submission_key, /:1$/);
        assert.equal(contexts[0].origin.kind, "preparation");
        assert.equal(contexts[0].origin.segment_kind, "new");
        assert.equal(contexts[0].origin.segment_index, 0);
        assert.match(contexts[0].origin.preparation_revision, /^sha256:[0-9a-f]{64}$/);
    });
});
