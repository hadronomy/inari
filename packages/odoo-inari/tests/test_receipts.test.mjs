import assert from "node:assert/strict";
import { test } from "node:test";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import { TestReceiptRunner } from "../inari_devices/static/src/test_receipt_runner.js";
import { MemoryRecoveryStore } from "../inari_devices/static/src/recovery_store.js";

const printer = { device_id: "printer-2", name: "Other printer" };
const channel = {
    binding: { device_id: "printer-2", binding_revision_id: "binding-2" },
    pos_session_id: "session-2",
};

function fixture({ submit } = {}) {
    const submissions = [];
    const client = {
        submit: async (context, jpeg) => {
            submissions.push({ context, jpeg });
            if (submit) return submit(context, jpeg);
            return {
                state: "accepted",
                print_intent_id: context.print_intent_id,
                device_id: context.device_id,
                print_job_id: `job-${submissions.length}`,
                state_version: 1,
            };
        },
        queryPrintJobs: async (ids) => ({
            jobs: ids.map((id) => ({
                print_intent_id: id,
                print_job_id: `job-${submissions.findIndex(({ context }) => context.print_intent_id === id) + 1}`,
                state: "output_confirmed",
                state_version: 3,
                confirmation_evidence: "spooler",
            })),
            missing_print_intent_ids: [],
            high_water_mark: 3,
        }),
    };
    const store = new MemoryRecoveryStore();
    const runner = new TestReceiptRunner({
        store,
        clientForContext: async () => client,
        cryptoApi: webcrypto,
        fetchApi: async (url) =>
            new Response(
                await readFile(
                    new URL(
                        `../inari_devices${url.replace("/inari_devices", "")}`,
                        import.meta.url,
                    ),
                ),
                {
                    headers: { "Content-Type": "image/jpeg" },
                },
            ),
    });
    return { runner, store, client, submissions };
}

test("both samples use the selected printer and separate durable Print Intents", async () => {
    const { runner, client, submissions, store } = fixture();
    await Promise.all([
        runner.print({ printer, channel, selection: "both", client }),
        runner.print({ printer, channel, selection: "both", client }),
    ]);
    assert.equal(submissions.length, 2);
    assert.notEqual(submissions[0].context.print_intent_id, submissions[1].context.print_intent_id);
    for (const { context, jpeg } of submissions) {
        assert.equal(context.device_id, printer.device_id);
        assert.equal(context.binding_revision_id, "binding-2");
        assert.equal(context.origin.pos_session_id, "session-2");
        assert.equal(context.origin.server_order_id, null);
        assert.equal(jpeg.type, "image/jpeg");
        assert.deepEqual([...new Uint8Array(await jpeg.slice(0, 2).arrayBuffer())], [255, 216]);
    }
    assert.equal((await store.list()).length, 2);
    await runner.refresh();
    assert.deepEqual(
        runner.rows.map(({ state }) => state),
        ["output_confirmed", "output_confirmed"],
    );
    assert.equal((await store.list()).length, 0);
    await runner.print({ printer, channel, selection: "both", client });
    assert.equal(submissions.length, 2);
});

test("reopening checks pending results without submitting another receipt", async () => {
    const { runner, client, submissions, store } = fixture();
    await runner.print({ printer, channel, selection: "mizona", client });
    const reopened = new TestReceiptRunner({
        store,
        clientForContext: async () => client,
        cryptoApi: webcrypto,
    });
    await reopened.restore(printer.device_id);
    assert.equal(reopened.rows[0].name, "MIZONA sample");
    await reopened.print({ printer, channel, selection: "both", client });
    assert.equal(submissions.length, 1);
    await reopened.refresh();
    assert.equal(reopened.rows[0].state, "output_confirmed");
    assert.equal(submissions.length, 1);
});

test("an unknown admission keeps the original intent and never reports printed", async () => {
    const { runner, client, submissions } = fixture({
        submit: async () => {
            throw new TypeError("Network lost");
        },
    });
    await runner.print({ printer, channel, selection: "inari", client });
    assert.equal(runner.rows[0].state, "pending_agent");
    await runner.print({ printer, channel, selection: "inari", client });
    assert.equal(submissions.length, 1);
});

test("no receipt enters the queue for a mismatched printer", async () => {
    const { runner, client, submissions } = fixture();
    await assert.rejects(
        runner.print({
            printer,
            channel: { ...channel, binding: { ...channel.binding, device_id: "another" } },
            selection: "both",
            client,
        }),
        /exact authorized printer/,
    );
    assert.equal(submissions.length, 0);
});
