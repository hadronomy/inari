import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { InariDeviceInputAdapter } from "../inari_devices/static/src/device_input_adapter.js";

const NOW = Date.parse("2026-09-03T12:00:00.000Z");

function binding(kind, values = {}) {
    return {
        authoritative: true,
        device_id: `${kind}_1`,
        binding_revision_id: `binding_${kind}_1`,
        certification_id: kind === "scale" ? "certification_1" : undefined,
        ...values,
    };
}

function scaleReading(sequence, values = {}) {
    return {
        agent_boot_id: "boot_1",
        device_id: "scale_1",
        sequence,
        observed_at: new Date(NOW).toISOString(),
        value_mantissa: "12345",
        resolution_mantissa: "1",
        decimal_exponent: -3,
        unit: "kg",
        stable: true,
        range_state: "valid",
        ...values,
    };
}

class ManualTimers {
    constructor() {
        this.nextId = 1;
        this.tasks = new Map();
    }

    setTimeout(callback, delay) {
        const id = this.nextId++;
        this.tasks.set(id, { callback, delay });
        return id;
    }

    clearTimeout(id) {
        this.tasks.delete(id);
    }

    runAll() {
        const tasks = [...this.tasks.values()];
        this.tasks.clear();
        for (const task of tasks) task.callback();
    }
}

function streamHarness() {
    const opened = [];
    const session = {
        state: { name: "idle", scaleActive: false },
        async setScaleActive(active) {
            this.state = { name: active ? "connected" : "idle", scaleActive: active };
            opened[0].onState(this.state);
            return this.state;
        },
        async close() {
            this.closed = true;
        },
    };
    return {
        opened,
        session,
        streams: {
            async open(request) {
                opened.push(request);
                return session;
            },
        },
    };
}

function scaleHarness(options = {}) {
    const accepted = [];
    const invalidated = [];
    const port = {
        isUnitCompatible: (unit) => unit === "kg",
        async accept(reading) {
            accepted.push(reading);
        },
        invalidate(reason) {
            invalidated.push(reason);
        },
        ...options,
    };
    return { accepted, invalidated, port };
}

describe("Inari Device input Adapter", () => {
    test("keeps scale decimals exact until the Odoo port accepts them", async () => {
        const stream = streamHarness();
        const scale = scaleHarness();
        const timers = new ManualTimers();
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scalePort: scale.port,
            now: () => NOW,
            timers,
        }).start({ scaleBinding: binding("scale") });

        await adapter.setScaleActive(true);
        await stream.opened[0].scale.handler(
            scaleReading(1, {
                value_mantissa: "9223372036854775807",
                resolution_mantissa: "2",
                decimal_exponent: -12,
            }),
        );
        assert.equal(adapter.snapshot().scaleReason, "stabilizing");
        await stream.opened[0].scale.handler(
            scaleReading(2, {
                value_mantissa: "9223372036854775806",
                resolution_mantissa: "2",
                decimal_exponent: -12,
            }),
        );

        assert.equal(adapter.snapshot().scaleValid, true);
        assert.equal(scale.accepted.length, 1);
        assert.equal(scale.accepted[0].decimal, "9223372.036854775806");
        assert.equal(typeof scale.accepted[0].mantissa, "bigint");
        await adapter.stop();
    });

    test("requires two stable readings within one resolution", async () => {
        const stream = streamHarness();
        const scale = scaleHarness();
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scalePort: scale.port,
            now: () => NOW,
        }).start({ scaleBinding: binding("scale") });
        await adapter.setScaleActive(true);

        await stream.opened[0].scale.handler(scaleReading(1));
        await stream.opened[0].scale.handler(scaleReading(2, { value_mantissa: "12347" }));
        await stream.opened[0].scale.handler(scaleReading(3, { value_mantissa: "12348" }));

        assert.equal(scale.accepted.length, 1);
        assert.equal(scale.accepted[0].decimal, "12.348");
        await adapter.stop();
    });

    test("invalidates stale, unstable, out-of-range, incompatible, and non-positive readings", async () => {
        const stream = streamHarness();
        const scale = scaleHarness();
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scalePort: scale.port,
            now: () => NOW,
        }).start({ scaleBinding: binding("scale") });
        await adapter.setScaleActive(true);

        const readings = [
            scaleReading(1, { observed_at: new Date(NOW - 1_001).toISOString() }),
            scaleReading(2, { stable: false }),
            scaleReading(3, { range_state: "overload" }),
            scaleReading(4, { unit: "lb" }),
            scaleReading(5, { value_mantissa: "0" }),
        ];
        // Sequential delivery matches the ordered Device Stream contract.
        // oxlint-disable-next-line no-await-in-loop
        for (const reading of readings) await stream.opened[0].scale.handler(reading);

        assert.deepEqual(scale.invalidated.slice(-5), [
            "stale",
            "unstable",
            "overload",
            "unit_incompatible",
            "non_positive",
        ]);
        assert.equal(adapter.snapshot().scaleValid, false);
        await adapter.stop();
    });

    test("expires an accepted reading one second after its observation", async () => {
        const stream = streamHarness();
        const scale = scaleHarness();
        const timers = new ManualTimers();
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scalePort: scale.port,
            now: () => NOW,
            timers,
        }).start({ scaleBinding: binding("scale") });
        await adapter.setScaleActive(true);
        await stream.opened[0].scale.handler(scaleReading(1));
        await stream.opened[0].scale.handler(scaleReading(2));

        assert.equal(adapter.snapshot().scaleValid, true);
        assert.deepEqual(
            [...timers.tasks.values()].map((task) => task.delay),
            [1_000],
        );
        timers.runAll();
        assert.equal(adapter.snapshot().scaleValid, false);
        assert.equal(adapter.snapshot().scaleReason, "stale");
        await adapter.stop();
    });

    test("invalidates the accepted reading when the Scale Lease is lost", async () => {
        const stream = streamHarness();
        const scale = scaleHarness();
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scalePort: scale.port,
            now: () => NOW,
        }).start({ scaleBinding: binding("scale") });
        await adapter.setScaleActive(true);
        await stream.opened[0].scale.handler(scaleReading(1));
        await stream.opened[0].scale.handler(scaleReading(2));

        stream.opened[0].onState({ name: "scale_lease_lost", scaleActive: false });

        assert.equal(adapter.snapshot().scaleValid, false);
        assert.equal(adapter.snapshot().scaleReason, "scale_lease_lost");
        assert.equal(scale.invalidated.at(-1), "scale_lease_lost");
        await adapter.stop();
    });

    test("requires a physical weight change after a reading is consumed", async () => {
        const stream = streamHarness();
        const scale = scaleHarness();
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scalePort: scale.port,
            now: () => NOW,
        }).start({ scaleBinding: binding("scale") });
        await adapter.setScaleActive(true);
        await stream.opened[0].scale.handler(scaleReading(1));
        await stream.opened[0].scale.handler(scaleReading(2));
        adapter.consumeScaleReading();

        await stream.opened[0].scale.handler(scaleReading(3));
        assert.equal(adapter.snapshot().scaleReason, "weight_unchanged");
        await stream.opened[0].scale.handler(scaleReading(4, { value_mantissa: "12346" }));
        await stream.opened[0].scale.handler(scaleReading(5, { value_mantissa: "12346" }));

        assert.equal(scale.accepted.at(-1).decimal, "12.346");
        await adapter.stop();
    });

    test("deduplicates barcode events by Agent Boot Identity, Device, and sequence", async () => {
        const stream = streamHarness();
        const scanned = [];
        const adapter = await new InariDeviceInputAdapter({
            streams: stream.streams,
            scannerPort: { scan: async (value) => scanned.push(value) },
        }).start({ scannerBinding: binding("scanner") });
        const handler = stream.opened[0].scanner.handler;

        await handler({ agent_boot_id: "boot_1", device_id: "scanner_1", sequence: 1, value: "A" });
        await handler({ agent_boot_id: "boot_1", device_id: "scanner_1", sequence: 1, value: "A" });
        await handler({ agent_boot_id: "boot_2", device_id: "scanner_1", sequence: 1, value: "B" });

        assert.deepEqual(scanned, ["A", "B"]);
        await adapter.stop();
        assert.equal(stream.session.closed, true);
    });
});
