import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { InariAgentError } from "../inari_devices/static/src/agent_client.js";
import {
    markPreparationSegments,
    markPreparationSource,
    PreparationPlanBook,
    preparationSource,
} from "../inari_devices/static/src/preparation_print.js";
import { PrintRecoveryCoordinator } from "../inari_devices/static/src/print_recovery.js";
import { MemoryRecoveryStore } from "../inari_devices/static/src/recovery_store.js";
import { materializeSubmissionContext } from "../inari_devices/static/src/submission_context.js";

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
        const client = {
            async submit(context) {
                contexts.push(context);
                if (contexts.length === 1) {
                    throw new InariAgentError("printer_offline", "Printer offline", {
                        status: 503,
                        retryable: true,
                    });
                }
                return {
                    state: "accepted",
                    print_intent_id: context.print_intent_id,
                    print_job_id: "job-1",
                    device_id: context.device_id,
                    state_version: 1,
                };
            },
        };
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore(),
            clientForContext: async () => client,
        });
        await recovery.restore();
        const { receipts } = source();
        const plan = await new PreparationPlanBook({
            randomUUID: () => "00000000-0000-0000-0000-000000000001",
        }).plan({
            binding: binding(),
            source: preparationSource(receipts[0]),
            posSessionId: 42,
        });
        renders += 1;
        const jpeg = new Blob(["jpeg"], { type: "image/jpeg" });
        const submissionContext = await materializeSubmissionContext(plan, jpeg);

        const failed = await recovery.enqueue({
            context: submissionContext,
            jpeg,
            client,
        });
        await recovery.act(plan.print_intent_id, "retry");
        const accepted = (await recovery.knownResult(plan.print_intent_id));

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
