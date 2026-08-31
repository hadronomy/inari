import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { InariAgentError } from "../inari_devices/static/src/agent_client.js";
import { PrintRecoveryCoordinator } from "../inari_devices/static/src/print_recovery.js";
import { MemoryRecoveryStore } from "../inari_devices/static/src/recovery_store.js";
import { createSubmissionContext } from "../inari_devices/static/src/submission_context.js";

function context({ kind = "pos", printIntentId = "pi_v1_receipt_1" } = {}) {
    return createSubmissionContext({
        contract_major: 1,
        print_intent_id: printIntentId,
        origin_submission_key: `order-1:${kind}:revision-1`,
        origin: {
            kind,
            pos_session_id: "session-1",
            offline_order_id: "order-1",
            server_order_id: null,
            document_kind: kind === "preparation" ? "preparation_ticket" : "customer_receipt",
            content_revision: "revision-1",
            ...(kind === "preparation"
                ? {
                      segment_kind: "new",
                      segment_index: 0,
                      preparation_revision: `sha256:${"1".repeat(64)}`,
                  }
                : {}),
        },
        binding_revision_id: "binding-1",
        device_id: "printer-1",
        copy_ordinal: 1,
    });
}

function accepted(submissionContext, stateVersion = 1) {
    return {
        state: "accepted",
        print_intent_id: submissionContext.print_intent_id,
        print_job_id: "job-1",
        device_id: submissionContext.device_id,
        state_version: stateVersion,
    };
}

function storedRecord(submissionContext, overrides = {}) {
    return {
        key: submissionContext.print_intent_id,
        context: submissionContext,
        descriptor: {
            order_reference: "Order 1",
            printer_name: "Kitchen",
        },
        state: "pending_agent",
        attention: true,
        print_job_id: null,
        state_version: null,
        retryable: false,
        failure_code: null,
        confirmation_evidence: null,
        resolution: null,
        updated_at: "2026-08-31T00:00:00.000Z",
        ...overrides,
    };
}

describe("Odoo print recovery", () => {
    test("reconciles an uncertain submission before retrying the exact content", async () => {
        const submissionContext = context();
        const jpeg = new Blob(["jpeg"], { type: "image/jpeg" });
        const attempts = [];
        const client = {
            async submit(value, document) {
                attempts.push({ value, document });
                if (attempts.length === 1) {
                    throw new TypeError("The connection closed before the response");
                }
                return accepted(value);
            },
            async queryPrintJobs(ids) {
                return {
                    jobs: [],
                    missing_print_intent_ids: ids,
                    high_water_mark: 0,
                };
            },
        };
        const store = new MemoryRecoveryStore();
        const recovery = new PrintRecoveryCoordinator({
            store,
            clientForContext: async () => client,
        });
        await recovery.restore();

        const uncertain = await recovery.enqueue({
            context: submissionContext,
            jpeg,
            client,
        });

        assert.equal(uncertain.state, "pending_agent");
        assert.deepEqual(recovery.snapshot()[0].actions, ["reconcile", "finish_without_ticket"]);

        await recovery.act(submissionContext.print_intent_id, "reconcile");
        assert.equal(recovery.knownResult(submissionContext.print_intent_id).state, "failed");
        assert.ok(recovery.snapshot()[0].actions.includes("retry"));

        await recovery.act(submissionContext.print_intent_id, "retry");

        assert.equal(recovery.knownResult(submissionContext.print_intent_id).accepted, true);
        assert.equal(attempts.length, 2);
        assert.equal(attempts[0].value, submissionContext);
        assert.equal(attempts[1].value, submissionContext);
        assert.equal(attempts[0].document, jpeg);
        assert.equal(attempts[1].document, jpeg);
        const records = await store.list();
        assert.equal(records[0].state, "accepted");
        assert.equal(Object.hasOwn(records[0], "jpeg"), false);
    });

    test("does not invent print content after a reload", async () => {
        const submissionContext = context();
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore([storedRecord(submissionContext)]),
            clientForContext: async () => ({
                async queryPrintJobs(ids) {
                    return {
                        jobs: [],
                        missing_print_intent_ids: ids,
                        high_water_mark: 0,
                    };
                },
            }),
        });

        await recovery.restore();
        await recovery.reconcile();

        const [task] = recovery.snapshot();
        assert.equal(task.state, "content_unavailable");
        assert.equal(task.content_available, false);
        assert.deepEqual(task.actions, ["finish_without_ticket"]);
    });

    test("hides a restored task after the Agent confirms output", async () => {
        const submissionContext = context();
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore([
                storedRecord(submissionContext, {
                    state: "accepted",
                    attention: false,
                    print_job_id: "job-1",
                    state_version: 1,
                }),
            ]),
            clientForContext: async () => ({
                async queryPrintJobs() {
                    return {
                        jobs: [
                            {
                                print_intent_id: submissionContext.print_intent_id,
                                print_job_id: "job-1",
                                device_id: submissionContext.device_id,
                                state: "output_confirmed",
                                state_version: 2,
                                error_code: null,
                                confirmation_evidence: "device_ack",
                            },
                        ],
                        missing_print_intent_ids: [],
                        high_water_mark: 2,
                    };
                },
            }),
        });

        await recovery.restore();
        await recovery.reconcile();

        assert.deepEqual(recovery.snapshot(), []);
        assert.equal(recovery.knownResult(submissionContext.print_intent_id).accepted, true);
        assert.equal(
            recovery.knownResult(submissionContext.print_intent_id).state,
            "output_confirmed",
        );
    });

    test("watches admitted work until the Agent reaches a physical outcome", async () => {
        const submissionContext = context();
        const scheduled = [];
        const client = {
            async submit(value) {
                return accepted(value);
            },
            async queryPrintJobs() {
                return {
                    jobs: [
                        {
                            print_intent_id: submissionContext.print_intent_id,
                            print_job_id: "job-1",
                            device_id: submissionContext.device_id,
                            state: "output_confirmed",
                            state_version: 2,
                            error_code: null,
                            confirmation_evidence: "device_ack",
                        },
                    ],
                    missing_print_intent_ids: [],
                    high_water_mark: 2,
                };
            },
        };
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore(),
            clientForContext: async () => client,
            schedule(callback) {
                scheduled.push(callback);
                return callback;
            },
            cancelSchedule() {},
        });
        recovery.startWatching();

        await recovery.enqueue({
            context: submissionContext,
            jpeg: new Blob(["jpeg"], { type: "image/jpeg" }),
            client,
        });

        assert.equal(scheduled.length, 1);
        await scheduled.shift()();
        assert.equal(
            recovery.knownResult(submissionContext.print_intent_id).state,
            "output_confirmed",
        );
        assert.equal(scheduled.length, 0);
    });

    test("blocks preparation progress until admission or explicit dismissal", async () => {
        const submissionContext = context({
            kind: "preparation",
            printIntentId: "pi_v1_preparation_1",
        });
        const settled = [];
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore(),
            clientForContext: async () => ({
                async submit() {
                    throw new InariAgentError("printer_offline", "Printer offline", {
                        status: 503,
                        retryable: true,
                    });
                },
            }),
            onPreparationSettled: async (result) => settled.push(result),
        });
        await recovery.restore();

        await recovery.enqueue({
            context: submissionContext,
            jpeg: new Blob(["jpeg"], { type: "image/jpeg" }),
        });

        assert.equal(recovery.blocksPreparation("order-1"), true);
        assert.equal(settled.length, 0);

        await recovery.act(submissionContext.print_intent_id, "finish_without_ticket");

        assert.equal(recovery.blocksPreparation("order-1"), false);
        assert.equal(settled.length, 1);
        assert.equal(settled[0].resolution, "finish_without_ticket");
    });

    test("never offers an automatic retry for an unknown physical outcome", async () => {
        const submissionContext = context({
            kind: "preparation",
            printIntentId: "pi_v1_preparation_2",
        });
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore([storedRecord(submissionContext)]),
            clientForContext: async () => ({
                async queryPrintJobs() {
                    return {
                        jobs: [
                            {
                                print_intent_id: submissionContext.print_intent_id,
                                print_job_id: "job-2",
                                device_id: submissionContext.device_id,
                                state: "outcome_unknown",
                                state_version: 4,
                                error_code: "confirmation_timeout",
                                confirmation_evidence: null,
                            },
                        ],
                        missing_print_intent_ids: [],
                        high_water_mark: 4,
                    };
                },
            }),
        });

        await recovery.restore();
        await recovery.reconcile();

        assert.deepEqual(recovery.snapshot()[0].actions, ["reconcile", "finish_without_ticket"]);
        assert.equal(recovery.blocksPreparation("order-1"), false);
    });
});
