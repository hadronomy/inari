import assert from "node:assert/strict";
import { test } from "node:test";
import { MemoryRecoveryStore } from "../inari_devices/static/src/recovery_store.js";
import { PrintRecoveryCoordinator } from "../inari_devices/static/src/print_recovery.js";
import { createSubmissionContext } from "../inari_devices/static/src/submission_context.js";

const context = createSubmissionContext({
    contract_major: 1, print_intent_id: "pi_v1_terminal_1", origin_submission_key: "receipt:1",
    binding_revision_id: "binding-old", device_id: "printer-1", copy_ordinal: 1,
    origin: { kind: "pos", pos_session_id: "session-1", offline_order_id: "order-1",
        server_order_id: null, document_kind: "customer_receipt", content_revision: "sha256:receipt" },
});

test("completed recovery leaves no live task and preserves identity across reload", async () => {
    let now = Date.now();
    const store = new MemoryRecoveryStore([], { now: () => now });
    const channel = { agent_id: "original-agent", agent_endpoint: "https://agent.example" };
    let resolvedChannel;
    const recovery = new PrintRecoveryCoordinator({ store, clientForContext: async (_, options) => {
        resolvedChannel = options.channel;
        return { queryPrintJobs: async () => ({ jobs: [{ print_intent_id: context.print_intent_id,
            print_job_id: "job-1", state: "output_confirmed", state_version: 2 }],
            missing_print_intent_ids: [], high_water_mark: 2 }) };
    } });
    await store.put({ key: context.print_intent_id, context, state: "accepted", print_job_id: "job-1",
        state_version: 1, descriptor: { agent_channel: channel } });
    await recovery.restore();
    await recovery.reconcile();
    assert.deepEqual(resolvedChannel, channel);
    assert.equal(recovery.entries.size, 0);
    assert.deepEqual(await store.list(), []);
    const restored = new PrintRecoveryCoordinator({ store, clientForContext: async () => null });
    await restored.restore();
    assert.equal(restored.entries.size, 0);
    assert.equal((await restored.knownResult(context.print_intent_id)).state, "output_confirmed");
    assert.equal((await restored.receiptContext({ ...context, ...context.origin })).print_intent_id, context.print_intent_id);
    now += 91 * 24 * 60 * 60 * 1000;
    assert.equal(await restored.knownResult(context.print_intent_id), null);
    assert.equal(store.settled.size, 0);
});

test("concurrent receipt clicks submit one Print Intent", async () => {
    let submissions = 0;
    const client = { submit: async () => {
        submissions += 1;
        return { state: "accepted", print_job_id: "job-1", state_version: 1 };
    } };
    const recovery = new PrintRecoveryCoordinator({
        store: new MemoryRecoveryStore(), clientForContext: async () => client,
    });
    const request = { context, jpeg: new Blob(["jpeg"], { type: "image/jpeg" }), client };
    const results = await Promise.all([recovery.enqueue(request), recovery.enqueue(request)]);
    assert.equal(submissions, 1);
    assert.equal(results[0].printIntentId, results[1].printIntentId);
});
