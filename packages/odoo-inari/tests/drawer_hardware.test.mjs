import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { InariAgentClient } from "../inari_devices/static/src/agent_client.js";
import { MemoryDrawerIntentStore } from "../inari_devices/static/src/drawer_intent_store.js";
import { InariHardwareAdapter } from "../inari_devices/static/src/hardware_adapter.js";

function binding() {
    return {
        authoritative: true,
        binding_revision_id: "binding-drawer-1",
        device_id: "printer-1",
    };
}

function credentials() {
    return {
        getAccessToken: async () => "access-token",
        createProof: async () => "proof",
    };
}

describe("Odoo Inari cash drawer", () => {
    test("submits one exact intent and waits for its physical result", async () => {
        const submissions = [];
        let queries = 0;
        const client = {
            async submitDrawerIntent(intent) {
                submissions.push(intent);
                return {
                    drawer_intent_id: intent.drawer_intent_id,
                    device_id: intent.device_id,
                    state: "accepted",
                    retryable: true,
                };
            },
            async queryDrawerIntents([intentId]) {
                queries += 1;
                return {
                    intents: [
                        {
                            drawer_intent_id: intentId,
                            device_id: "printer-1",
                            state: queries === 1 ? "in_progress" : "succeeded",
                            retryable: false,
                        },
                    ],
                    missing_drawer_intent_ids: [],
                };
            },
        };
        const adapter = new InariHardwareAdapter({
            clientForBinding: async () => client,
            randomUUID: () => "intent-1",
            now: () => 10,
            sleep: async () => {},
        });
        await adapter.attach({ binding: binding(), posSessionId: 9 });

        const result = await adapter.openDrawer({ action: false });

        assert.equal(result.state, "succeeded");
        assert.equal(result.accepted, true);
        assert.equal(submissions.length, 1);
        assert.deepEqual(submissions[0], {
            contract_major: 1,
            drawer_intent_id: "drawer-intent-1",
            binding_revision_id: "binding-drawer-1",
            device_id: "printer-1",
            pos_session_id: "9",
            action_sequence: 10,
            reason: "payment",
        });
    });

    test("keeps one pre-I/O identity for an explicit retry", async () => {
        const identities = [];
        let available = false;
        const client = {
            async submitDrawerIntent(intent) {
                identities.push(intent.drawer_intent_id);
                if (!available) {
                    throw new TypeError("network unavailable");
                }
                return {
                    drawer_intent_id: intent.drawer_intent_id,
                    device_id: intent.device_id,
                    state: "succeeded",
                    retryable: false,
                };
            },
            async queryDrawerIntents([intentId]) {
                return {
                    intents: [],
                    missing_drawer_intent_ids: [intentId],
                };
            },
        };
        const adapter = new InariHardwareAdapter({
            clientForBinding: async () => client,
            randomUUID: () => "retry-1",
            now: () => 20,
        });
        await adapter.attach({ binding: binding(), posSessionId: 9 });

        const first = await adapter.openDrawer({ action: true });
        available = true;
        const second = await adapter.openDrawer({ action: true });

        assert.equal(first.state, "failed");
        assert.equal(first.retryable, true);
        assert.equal(second.state, "succeeded");
        assert.deepEqual(identities, ["drawer-retry-1", "drawer-retry-1"]);
    });

    test("blocks a second pulse after an uncertain outcome", async () => {
        let submissions = 0;
        const client = {
            async submitDrawerIntent() {
                submissions += 1;
                throw new TypeError("connection lost");
            },
            async queryDrawerIntents() {
                throw new TypeError("connection lost");
            },
        };
        const adapter = new InariHardwareAdapter({
            clientForBinding: async () => client,
            randomUUID: () => "unknown-1",
            now: () => 30,
        });
        await adapter.attach({ binding: binding(), posSessionId: 9 });

        const first = await adapter.openDrawer();
        const second = await adapter.openDrawer();

        assert.equal(first.state, "outcome_unknown");
        assert.equal(second.error_code, "manager_review_required");
        assert.equal(submissions, 1);
    });

    test("never submits before the recovery journal is durable", async () => {
        let submissions = 0;
        const adapter = new InariHardwareAdapter({
            clientForBinding: async () => ({
                async submitDrawerIntent() {
                    submissions += 1;
                },
            }),
            randomUUID: () => "unsafe-1",
            now: () => 35,
            store: {
                async get() {
                    return null;
                },
                async put() {
                    throw new Error("storage unavailable");
                },
            },
        });
        await adapter.attach({ binding: binding(), posSessionId: 9 });

        const result = await adapter.openDrawer();

        assert.equal(result.state, "failed");
        assert.equal(result.retryable, false);
        assert.equal(result.error_code, "drawer_journal_unavailable");
        assert.equal(submissions, 0);
    });

    test("restores the uncertain safety block after reload", async () => {
        const store = new MemoryDrawerIntentStore();
        const client = {
            async submitDrawerIntent() {
                throw new TypeError("connection lost");
            },
            async queryDrawerIntents() {
                throw new TypeError("connection lost");
            },
        };
        const first = new InariHardwareAdapter({
            clientForBinding: async () => client,
            randomUUID: () => "persisted-1",
            now: () => 40,
            store,
        });
        await first.attach({ binding: binding(), posSessionId: 9 });
        await first.openDrawer();

        let submissions = 0;
        const restored = new InariHardwareAdapter({
            clientForBinding: async () => ({
                async submitDrawerIntent() {
                    submissions += 1;
                },
            }),
            store,
        });
        await restored.attach({ binding: binding(), posSessionId: 9 });

        const result = await restored.openDrawer();

        assert.equal(result.state, "outcome_unknown");
        assert.equal(result.error_code, "manager_review_required");
        assert.equal(submissions, 0);
    });

    test("uses the protected Drawer Intent wire contract", async () => {
        const requests = [];
        const client = new InariAgentClient({
            baseUrl: "https://agent.example",
            credentials: credentials(),
            cryptoApi: {
                getRandomValues(bytes) {
                    bytes.fill(1);
                    return bytes;
                },
            },
            fetchApi: async (url, options) => {
                requests.push({ url, options });
                return Response.json({ ok: true });
            },
        });
        const intent = {
            contract_major: 1,
            drawer_intent_id: "drawer-1",
            binding_revision_id: "binding-1",
            device_id: "printer-1",
            pos_session_id: "9",
            action_sequence: 1,
            reason: "manual_open",
        };

        await client.submitDrawerIntent(intent);
        await client.queryDrawerIntents(["drawer-1", "drawer-1"]);

        assert.equal(requests[0].url, "https://agent.example/v1/drawer-intents");
        assert.equal(requests[0].options.headers.get("Idempotency-Key"), "drawer-1");
        assert.equal(
            requests[0].options.body,
            '{"action_sequence":1,"binding_revision_id":"binding-1","contract_major":1,"device_id":"printer-1","drawer_intent_id":"drawer-1","pos_session_id":"9","reason":"manual_open"}',
        );
        assert.equal(requests[1].url, "https://agent.example/v1/drawer-intents/query");
        assert.equal(requests[1].options.body, '{"drawer_intent_ids":["drawer-1"]}');
    });
});
