import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { InariAgentClient, canonicalJson } from "../inari_devices/static/src/agent_client.js";
import { MemoryContextStore } from "../inari_devices/static/src/context_store.js";
import { ReceiptQueue } from "../inari_devices/static/src/receipt_queue.js";
import {
    createSubmissionContext,
    envelopeFor,
} from "../inari_devices/static/src/submission_context.js";

function submissionContext() {
    return createSubmissionContext({
        contract_major: 1,
        print_intent_id: "pi_v1_receipt_1",
        origin_submission_key: "order-1:customer_receipt:revision-1",
        origin: {
            kind: "pos",
            pos_session_id: "session-1",
            offline_order_id: "order-1",
            server_order_id: null,
            document_kind: "customer_receipt",
            content_revision: "revision-1",
        },
        binding_revision_id: "binding-1",
        device_id: "printer-1",
        copy_ordinal: 1,
    });
}

describe("Odoo Inari receipt core", () => {
    test("uses the exact Agent envelope", () => {
        const context = submissionContext();
        assert.deepEqual(Object.keys(context).toSorted(), [
            "binding_revision_id",
            "contract_major",
            "copy_ordinal",
            "device_id",
            "origin",
            "origin_submission_key",
            "print_intent_id",
        ]);
        assert.equal(
            canonicalJson(envelopeFor(context)),
            JSON.stringify({
                context: {
                    binding_revision_id: "binding-1",
                    contract_major: 1,
                    copy_ordinal: 1,
                    device_id: "printer-1",
                    origin: {
                        content_revision: "revision-1",
                        document_kind: "customer_receipt",
                        kind: "pos",
                        offline_order_id: "order-1",
                        pos_session_id: "session-1",
                        server_order_id: null,
                    },
                    origin_submission_key: "order-1:customer_receipt:revision-1",
                    print_intent_id: "pi_v1_receipt_1",
                },
                contract_major: 1,
                media_type: "image/jpeg",
                operation: "receipt_image",
            }),
        );
        assert.throws(() =>
            createSubmissionContext({
                ...context,
                actor_id: "operator-1",
            }),
        );
    });

    test("retries one DPoP nonce challenge without changing receipt identity", async () => {
        const fetches = [];
        const proofs = [];
        const client = new InariAgentClient({
            baseUrl: "https://agent.example",
            credentials: {
                getAccessToken: async () => "access-token",
                createProof: async (values) => {
                    proofs.push(values);
                    return `proof-${proofs.length}`;
                },
            },
            cryptoApi: {
                getRandomValues(bytes) {
                    bytes.fill(9);
                    return bytes;
                },
            },
            fetchApi: async (_url, options) => {
                fetches.push(options);
                if (fetches.length === 1) {
                    return new Response("{}", {
                        status: 401,
                        headers: {
                            "DPoP-Nonce": "agent-nonce",
                            "WWW-Authenticate": 'DPoP realm="inari", error="use_dpop_nonce"',
                        },
                    });
                }
                return Response.json(
                    {
                        state: "accepted",
                        print_intent_id: "pi_v1_receipt_1",
                        print_job_id: "job-1",
                    },
                    { status: 202 },
                );
            },
        });

        const result = await client.submit(
            submissionContext(),
            new Blob(["jpeg"], { type: "image/jpeg" }),
        );

        assert.equal(result.state, "accepted");
        assert.equal(fetches.length, 2);
        assert.equal(proofs.length, 2);
        assert.equal(proofs[1].nonce, "agent-nonce");
        assert.equal(
            fetches[0].headers.get("Idempotency-Key"),
            fetches[1].headers.get("Idempotency-Key"),
        );
        assert.equal(
            fetches[0].body.get("envelope").type.startsWith("application/json"),
            true,
        );
        assert.equal(fetches[0].body.get("document").type, "image/jpeg");
    });

    test("queries Print Jobs by stable Print Intent identity", async () => {
        const requests = [];
        const client = new InariAgentClient({
            baseUrl: "https://agent.example",
            credentials: {
                getAccessToken: async () => "access-token",
                createProof: async () => "proof",
            },
            cryptoApi: {
                getRandomValues(bytes) {
                    bytes.fill(9);
                    return bytes;
                },
            },
            fetchApi: async (url, options) => {
                requests.push({ url, options });
                return Response.json({
                    jobs: [],
                    missing_print_intent_ids: ["intent-1"],
                    high_water_mark: 0,
                });
            },
        });

        const result = await client.queryPrintJobs(["intent-1", "intent-1"]);

        assert.deepEqual(result.missing_print_intent_ids, ["intent-1"]);
        assert.equal(requests[0].url, "https://agent.example/v1/jobs/query");
        assert.equal(requests[0].options.headers.get("Content-Type"), "application/json");
        assert.equal(requests[0].options.body, '{"print_intent_ids":["intent-1"]}');
    });

    test("keeps one immutable context through a failed submission and retry", async () => {
        const contexts = [];
        const queue = new ReceiptQueue({
            contextStore: new MemoryContextStore(),
            submit: async (context) => {
                contexts.push(context);
                if (contexts.length === 1) {
                    throw new Error("offline");
                }
                return { state: "accepted", print_job_id: "job-1" };
            },
        });
        const context = submissionContext();

        const entry = await queue.enqueue(context, new Blob(["jpeg"], { type: "image/jpeg" }));
        await queue.retry(entry.key);

        assert.equal(contexts.length, 2);
        assert.equal(contexts[0], context);
        assert.equal(contexts[1], context);
        assert.equal(queue.snapshot()[0].state, "accepted");
    });
});
