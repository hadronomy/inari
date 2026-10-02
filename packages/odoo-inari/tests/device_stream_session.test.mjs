import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { InariAgentClient, canonicalJson } from "../inari_devices/static/src/agent_client.js";
import {
    MemoryTransportCandidateStore,
    MemoryTransportHintHub,
} from "../inari_devices/static/src/device_stream_coordination.js";
import {
    DeviceStreamProtocolError,
    readSseFrames,
} from "../inari_devices/static/src/device_stream_protocol.js";
import { BrowserDeviceStreams } from "../inari_devices/static/src/device_stream_session.js";

const NOW = "2026-09-03T12:00:00.000Z";

function base64Url(value) {
    return Buffer.from(value).toString("base64url");
}

async function signingIdentity() {
    const keys = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
    const rawPublicKey = new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey));
    const digest = Buffer.from(await crypto.subtle.digest("SHA-256", rawPublicKey)).toString("hex");
    const keyId = `kid_${digest.slice(0, 12)}`;
    return {
        agentId: `agt_${digest.slice(0, 24)}`,
        keyId,
        privateKey: keys.privateKey,
        publicJwk: {
            kty: "OKP",
            crv: "Ed25519",
            alg: "EdDSA",
            use: "sig",
            kid: keyId,
            x: base64Url(rawPublicKey),
        },
    };
}

async function signedMessage(identity, lease, kind, streamSequence, payload) {
    const unsigned = {
        contract_major: 1,
        kind,
        stream_sequence: streamSequence,
        generation: lease.generation,
        occurred_at: NOW,
        payload,
        signer_key_id: identity.keyId,
    };
    const protectedPart = base64Url(
        JSON.stringify({ alg: "EdDSA", kid: identity.keyId, typ: "inari-agent-event+jws" }),
    );
    const payloadPart = base64Url(canonicalJson(unsigned));
    const signature = await crypto.subtle.sign(
        "Ed25519",
        identity.privateKey,
        new TextEncoder().encode(`${protectedPart}.${payloadPart}`),
    );
    return {
        ...unsigned,
        signature: `${protectedPart}.${payloadPart}.${base64Url(signature)}`,
    };
}

function sse(message, { corruptSignature = false } = {}) {
    const value = structuredClone(message);
    if (corruptSignature) {
        const parts = value.signature.split(".");
        parts[2] = `${parts[2].startsWith("A") ? "B" : "A"}${parts[2].slice(1)}`;
        value.signature = parts.join(".");
    }
    const id = ["scale_reading", "barcode"].includes(value.kind)
        ? `id: ${value.stream_sequence}\n`
        : "";
    return `event: ${value.kind}\n${id}data: ${JSON.stringify(value)}\n\n`;
}

function leaseFor(identity, holderId, selections, index) {
    return {
        ok: true,
        lease_id: `lease_${index}`,
        subscription_id: `subscription_${index}`,
        holder_id: holderId,
        generation: index,
        scope_digest: `scope_${index}`,
        agent_id: identity.agentId,
        agent_boot_id: "boot_1",
        client_grant_id: "grant_1",
        selections: structuredClone(selections),
        issued_at: NOW,
        expires_at: "2026-09-03T12:01:00.000Z",
        renew_after_ms: 60_000,
        heartbeat_interval_ms: 15_000,
        signing_public_jwk: identity.publicJwk,
    };
}

function readyPayload(lease, identity) {
    return {
        lease_id: lease.lease_id,
        subscription_id: lease.subscription_id,
        scope_digest: lease.scope_digest,
        agent_id: lease.agent_id,
        agent_boot_id: lease.agent_boot_id,
        client_grant_id: lease.client_grant_id,
        current_sequence: 0,
        high_water_mark: 0,
        device_cursors: {},
        signing_public_jwk: identity.publicJwk,
    };
}

function dataIdentity(lease, selection, sequence) {
    return {
        lease_id: lease.lease_id,
        subscription_id: lease.subscription_id,
        scope_digest: lease.scope_digest,
        agent_id: lease.agent_id,
        agent_boot_id: lease.agent_boot_id,
        client_grant_id: lease.client_grant_id,
        device_id: selection.device_id,
        binding_revision_id: selection.binding_revision_id,
        agent_sequence: sequence,
        sequence,
        monotonic_ms: sequence,
        observed_at: NOW,
    };
}

function streamResponse(initial, signal) {
    let controller;
    const body = new ReadableStream({
        start(value) {
            controller = value;
            value.enqueue(new TextEncoder().encode(initial));
            signal?.addEventListener("abort", () => {
                try {
                    value.error(new DOMException("Stream closed", "AbortError"));
                } catch {
                    // The stream already ended.
                }
            });
        },
    });
    return {
        controller: () => controller,
        response: new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    };
}

async function waitFor(predicate, message = "condition") {
    for (let attempt = 0; attempt < 100; attempt += 1) {
        if (predicate()) return;
        // Let the stream reader and its callback chain advance.
        // oxlint-disable-next-line no-await-in-loop
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
    assert.fail(`Timed out while waiting for ${message}`);
}

class ManualTimers {
    constructor() {
        this.nextId = 1;
        this.tasks = new Map();
    }

    setTimeout(callback, delay) {
        const id = this.nextId;
        this.nextId += 1;
        this.tasks.set(id, { callback, delay });
        return id;
    }

    clearTimeout(id) {
        this.tasks.delete(id);
    }

    runDelay(delay) {
        const task = [...this.tasks].find(([, value]) => value.delay === delay);
        assert.ok(task, `No timer uses the ${delay} ms delay`);
        this.tasks.delete(task[0]);
        task[1].callback();
    }
}

async function fakeStreamClient(identity) {
    const acquisitions = [];
    const releases = [];
    const scaleAcquisitions = [];
    const acknowledgements = [];
    const streams = [];
    const readyByLease = new Map();
    return {
        authorizationVersion: 1,
        acquisitions,
        releases,
        scaleAcquisitions,
        acknowledgements,
        streams,
        async acquireEventLease({ holder_id: holderId, selections }) {
            acquisitions.push(structuredClone(selections));
            const lease = leaseFor(identity, holderId, selections, acquisitions.length);
            readyByLease.set(
                lease.lease_id,
                sse(
                    await signedMessage(identity, lease, "ready", 0, readyPayload(lease, identity)),
                ),
            );
            return lease;
        },
        async releaseEventLease(lease) {
            releases.push(lease.lease_id);
        },
        async renewEventLease(lease) {
            return lease;
        },
        async acquireScaleLease(lease) {
            const selection = lease.selections.find((value) => value.kind === "scale");
            scaleAcquisitions.push(lease.lease_id);
            return {
                ok: true,
                scale_lease_id: `scale_${lease.generation}`,
                event_lease_id: lease.lease_id,
                device_id: selection.device_id,
                binding_revision_id: selection.binding_revision_id,
                generation: lease.generation,
                issued_at: NOW,
                expires_at: "2026-09-03T12:00:30.000Z",
                renew_after_ms: 60_000,
            };
        },
        async releaseScaleLease() {},
        async renewScaleLease(lease) {
            return lease;
        },
        async openEventStream({ eventLease, signal, scaleLease, lastEventId }) {
            const stream = streamResponse(readyByLease.get(eventLease.lease_id), signal);
            streams.push({ eventLease, scaleLease, lastEventId, ...stream });
            return stream.response;
        },
        async acknowledgeBarcodeEvents(lease, values) {
            acknowledgements.push({ lease: lease.lease_id, values: structuredClone(values) });
            return { ok: true };
        },
    };
}

function scannerSelection(handler) {
    return {
        device_id: "scanner_1",
        binding_revision_id: "binding_scanner_1",
        handler,
    };
}

function scaleSelection(handler) {
    return {
        device_id: "scale_1",
        binding_revision_id: "binding_scale_1",
        certification_id: "certification_1",
        handler,
    };
}

function browserStreams(identity, client, options = {}) {
    return new BrowserDeviceStreams({
        client,
        pairedAgentId: identity.agentId,
        coordinationScope: "shop:1:pos:42",
        candidateStore: options.candidateStore || new MemoryTransportCandidateStore(),
        hintChannelFactory:
            options.hintChannelFactory ||
            (() => ({ subscribe: () => () => {}, publish() {}, close() {} })),
        cryptoApi: options.cryptoApi || crypto,
        timers: options.timers || globalThis,
        random: () => 0,
    });
}

describe("Odoo browser Device Streams", () => {
    test("uses the exact protected Agent endpoints and stream headers", async () => {
        const requests = [];
        const client = new InariAgentClient({
            baseUrl: "https://agent.example",
            credentials: {
                getAccessToken: async () => "access-token",
                createProof: async () => "proof",
            },
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
        const eventLease = {
            lease_id: "lease_1",
            subscription_id: "subscription_1",
            generation: 2,
        };
        const scaleLease = { scale_lease_id: "scale_1", generation: 3 };

        await client.acquireEventLease({
            holder_id: "holder_abcdefghijkl",
            selections: [
                {
                    kind: "scanner",
                    device_id: "scanner_1",
                    binding_revision_id: "binding_1",
                },
            ],
        });
        await client.renewEventLease(eventLease);
        await client.releaseEventLease(eventLease);
        await client.acquireScaleLease(eventLease);
        await client.renewScaleLease(scaleLease);
        await client.releaseScaleLease(scaleLease);
        await client.openEventStream({ eventLease, scaleLease, lastEventId: 9 });
        await client.acknowledgeBarcodeEvents(eventLease, [
            { device_id: "scanner_1", sequence: 10 },
        ]);

        assert.deepEqual(
            requests.map(({ url, options }) => `${options.method} ${new URL(url).pathname}`),
            [
                "POST /v1/events/lease",
                "POST /v1/events/lease/renew",
                "DELETE /v1/events/lease",
                "POST /v1/events/scale-lease",
                "POST /v1/events/scale-lease/renew",
                "DELETE /v1/events/scale-lease",
                "GET /v1/events",
                "POST /v1/events/ack",
            ],
        );
        assert.equal(
            requests[0].options.body,
            '{"contract_major":1,"holder_id":"holder_abcdefghijkl","selections":[{"binding_revision_id":"binding_1","certification_id":null,"device_id":"scanner_1","kind":"scanner"}]}',
        );
        assert.equal(requests[6].options.headers.get("X-Inari-Event-Lease"), "lease_1");
        assert.equal(
            requests[6].options.headers.get("X-Inari-Event-Subscription"),
            "subscription_1",
        );
        assert.equal(requests[6].options.headers.get("X-Inari-Scale-Lease"), "scale_1");
        assert.equal(requests[6].options.headers.get("Last-Event-ID"), "9");
        assert.equal(new URL(requests[6].url).search, "");
        assert.equal(
            requests[7].options.body,
            '{"acknowledgements":[{"device_id":"scanner_1","sequence":10}],"contract_major":1,"generation":2,"lease_id":"lease_1","subscription_id":"subscription_1"}',
        );
    });

    test("reconciles before data and acknowledges a Barcode Event after its handler", async () => {
        const identity = await signingIdentity();
        const client = await fakeStreamClient(identity);
        const order = [];
        const cryptoApi = {
            subtle: crypto.subtle,
            getRandomValues(bytes) {
                bytes.fill(0);
                bytes[0] = 252;
                return bytes;
            },
        };
        const session = await browserStreams(identity, client, { cryptoApi }).open({
            scanner: scannerSelection(async (payload) => {
                order.push(`barcode:${payload.value}`);
            }),
            reconcile: async ({ reason }) => order.push(`reconcile:${reason}`),
        });
        const lease = client.streams[0].eventLease;
        assert.equal(lease.holder_id.startsWith("_"), true);
        const selection = lease.selections[0];
        const barcode = await signedMessage(identity, lease, "barcode", 1, {
            ...dataIdentity(lease, selection, 1),
            symbology: "ean13",
            value: "5012345678900",
        });

        client.streams[0].controller().enqueue(new TextEncoder().encode(sse(barcode)));
        await waitFor(() => client.acknowledgements.length === 1, "Barcode acknowledgement");

        const afterGap = await signedMessage(identity, lease, "barcode", 3, {
            ...dataIdentity(lease, selection, 2),
            symbology: "ean13",
            value: "5012345678901",
        });
        client.streams[0].controller().enqueue(new TextEncoder().encode(sse(afterGap)));
        await waitFor(() => client.acknowledgements.length === 2, "post-gap acknowledgement");

        assert.deepEqual(order, [
            "reconcile:initial",
            "barcode:5012345678900",
            "reconcile:sequence_gap",
            "barcode:5012345678901",
        ]);
        assert.deepEqual(client.acknowledgements[0].values, [
            { device_id: "scanner_1", sequence: 1 },
        ]);
        await session.close();
    });

    test("retries an acknowledgement without applying the Barcode Event twice", async () => {
        const identity = await signingIdentity();
        const client = await fakeStreamClient(identity);
        let acknowledgementAttempts = 0;
        client.acknowledgeBarcodeEvents = async (lease, values) => {
            acknowledgementAttempts += 1;
            if (acknowledgementAttempts === 1) throw new TypeError("connection lost");
            client.acknowledgements.push({
                lease: lease.lease_id,
                values: structuredClone(values),
            });
            return { ok: true };
        };
        const handled = [];
        const session = await browserStreams(identity, client).open({
            scanner: scannerSelection(async (payload) => handled.push(payload.value)),
            reconcile: async () => {},
        });
        const lease = client.streams[0].eventLease;
        const message = await signedMessage(identity, lease, "barcode", 1, {
            ...dataIdentity(lease, lease.selections[0], 1),
            symbology: "code128",
            value: "REPLAY-1",
        });

        client.streams[0].controller().enqueue(new TextEncoder().encode(sse(message)));
        await waitFor(() => client.streams.length === 2, "stream reconnect");
        client.streams[1].controller().enqueue(new TextEncoder().encode(sse(message)));
        await waitFor(() => client.acknowledgements.length === 1, "retried acknowledgement");

        assert.deepEqual(handled, ["REPLAY-1"]);
        assert.equal(acknowledgementAttempts, 2);
        assert.deepEqual(client.streams[1].lastEventId, null);
        await session.close();
    });

    test("reconnects when an otherwise valid stream reaches the idle limit", async () => {
        const identity = await signingIdentity();
        const client = await fakeStreamClient(identity);
        const timers = new ManualTimers();
        const session = await browserStreams(identity, client, { timers }).open({
            scanner: scannerSelection(async () => {}),
            reconcile: async () => {},
        });

        timers.runDelay(30_000);
        await waitFor(() => session.snapshot().name === "reconnecting", "idle state");
        assert.equal(session.snapshot().error.code, "stream_idle");
        timers.runDelay(0);
        await waitFor(
            () => client.streams.length === 2 && session.snapshot().name === "connected",
            "idle reconnect",
        );

        assert.equal(session.snapshot().name, "connected");
        await session.close();
    });

    test("retries leadership after a Scale Lease transport failure", async () => {
        const identity = await signingIdentity();
        const client = await fakeStreamClient(identity);
        const timers = new ManualTimers();
        client.acquireScaleLease = async () => {
            throw new TypeError("connection lost");
        };

        const session = await browserStreams(identity, client, { timers }).open({
            scanner: scannerSelection(async () => {}),
            scale: scaleSelection(async () => {}),
            reconcile: async () => {},
            scaleActive: true,
        });

        assert.equal(session.snapshot().name, "reconnecting");
        assert.equal(session.snapshot().scaleActive, true);
        assert.equal(session.snapshot().error.code, "agent_unavailable");
        assert.deepEqual(client.releases, ["lease_1"]);
        assert.equal(client.streams.length, 0);
        await session.close();
    });

    test("rejects a forged message before it reaches the Device handler", async () => {
        const identity = await signingIdentity();
        const client = await fakeStreamClient(identity);
        const handled = [];
        const session = await browserStreams(identity, client).open({
            scanner: scannerSelection(async (payload) => handled.push(payload)),
            reconcile: async () => {},
        });
        const lease = client.streams[0].eventLease;
        const barcode = await signedMessage(identity, lease, "barcode", 1, {
            ...dataIdentity(lease, lease.selections[0], 1),
            symbology: "code128",
            value: "INARI-1",
        });

        client.streams[0]
            .controller()
            .enqueue(new TextEncoder().encode(sse(barcode, { corruptSignature: true })));
        await waitFor(() => session.snapshot().name === "error", "signature error state");

        assert.equal(session.snapshot().error.code, "stream_signature_invalid");
        assert.deepEqual(handled, []);
        assert.deepEqual(client.acknowledgements, []);
        await session.close();
    });

    test("uses one browser candidate and leaves the Agent Lease as authority", async () => {
        const identity = await signingIdentity();
        const store = new MemoryTransportCandidateStore();
        const hints = new MemoryTransportHintHub();
        const firstClient = await fakeStreamClient(identity);
        const secondClient = await fakeStreamClient(identity);
        const options = {
            candidateStore: store,
            hintChannelFactory: (scope) => hints.channel(scope),
        };
        const first = await browserStreams(identity, firstClient, options).open({
            scanner: scannerSelection(async () => {}),
            reconcile: async () => {},
        });
        const second = await browserStreams(identity, secondClient, options).open({
            scanner: scannerSelection(async () => {}),
            reconcile: async () => {},
        });

        assert.equal(first.snapshot().name, "connected");
        assert.equal(second.snapshot().name, "follower");
        assert.equal(firstClient.acquisitions.length, 1);
        assert.equal(secondClient.acquisitions.length, 0);
        await second.close();
        await first.close();
    });

    test("rebuilds the Event Lease when Scale Lease activity changes", async () => {
        const identity = await signingIdentity();
        const client = await fakeStreamClient(identity);
        const scaleValues = [];
        const session = await browserStreams(identity, client).open({
            scanner: scannerSelection(async () => {}),
            scale: scaleSelection(async (payload) => scaleValues.push(payload.value_mantissa)),
            reconcile: async () => {},
            scaleActive: false,
        });

        await session.setScaleActive(true);
        const lease = client.streams[1].eventLease;
        const selection = lease.selections.find((value) => value.kind === "scale");
        const reading = await signedMessage(identity, lease, "scale_reading", 1, {
            ...dataIdentity(lease, selection, 1),
            certification_id: "certification_1",
            value_mantissa: "9223372036854775807",
            resolution_mantissa: "1",
            decimal_exponent: -3,
            unit: "kg",
            stable: true,
            range_state: "valid",
        });
        client.streams[1].controller().enqueue(new TextEncoder().encode(sse(reading)));
        await waitFor(() => scaleValues.length === 1, "Scale Reading");

        assert.deepEqual(
            client.acquisitions.map((selections) => selections.map((value) => value.kind)),
            [["scanner"], ["scanner", "scale"]],
        );
        assert.deepEqual(client.scaleAcquisitions, ["lease_2"]);
        assert.deepEqual(client.releases, ["lease_1"]);
        assert.deepEqual(scaleValues, ["9223372036854775807"]);
        await session.close();
    });

    test("parses split SSE chunks and preserves stream abort errors", async () => {
        const encoder = new TextEncoder();
        const split = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode('event: heartbeat\r\ndata: {"ok":'));
                controller.enqueue(encoder.encode("true}\r\n\r\n"));
                controller.close();
            },
        });
        const frames = [];
        for await (const frame of readSseFrames(split)) frames.push(frame);
        assert.deepEqual(frames, [{ event: "heartbeat", id: null, data: '{"ok":true}' }]);

        const aborted = new ReadableStream({
            start(controller) {
                controller.error(new DOMException("closed", "AbortError"));
            },
        });
        await assert.rejects(readSseFrames(aborted).next(), (error) => {
            assert.equal(error.name, "AbortError");
            assert.equal(error instanceof DeviceStreamProtocolError, false);
            return true;
        });
    });
});
