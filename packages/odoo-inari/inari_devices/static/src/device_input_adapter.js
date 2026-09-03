/** @odoo-module */

const MAX_SCALE_AGE_MS = 1_000;
const MAX_FUTURE_SKEW_MS = 1_000;

function bindingSelection(binding, kind, handler) {
    if (!binding || binding.authoritative !== true) {
        return null;
    }
    const selection = {
        device_id: binding.device_id,
        binding_revision_id: binding.binding_revision_id,
        handler,
    };
    if (kind === "scale") selection.certification_id = binding.certification_id;
    return Object.freeze(selection);
}

function powerOfTen(exponent) {
    return 10n ** BigInt(exponent);
}

function alignMantissa(reading, exponent) {
    return reading.mantissa * powerOfTen(reading.exponent - exponent);
}

function distanceWithinResolution(first, second) {
    const exponent = Math.min(first.exponent, second.exponent);
    const distance = alignMantissa(first, exponent) - alignMantissa(second, exponent);
    const firstResolution = first.resolution * powerOfTen(first.exponent - exponent);
    const secondResolution = second.resolution * powerOfTen(second.exponent - exponent);
    const allowed = firstResolution > secondResolution ? firstResolution : secondResolution;
    return (distance < 0n ? -distance : distance) <= allowed;
}

function sameExactValue(first, second) {
    if (!first || !second || first.unit !== second.unit) return false;
    const exponent = Math.min(first.exponent, second.exponent);
    return alignMantissa(first, exponent) === alignMantissa(second, exponent);
}

function decimalString(mantissa, exponent) {
    const negative = mantissa < 0n;
    const digits = (negative ? -mantissa : mantissa).toString();
    let value;
    if (exponent >= 0) {
        value = `${digits}${"0".repeat(exponent)}`;
    } else {
        const point = digits.length + exponent;
        value =
            point > 0
                ? `${digits.slice(0, point)}.${digits.slice(point)}`
                : `0.${"0".repeat(-point)}${digits}`;
    }
    return negative ? `-${value}` : value;
}

function exactReading(payload) {
    return Object.freeze({
        agentBootId: payload.agent_boot_id,
        deviceId: payload.device_id,
        sequence: payload.sequence,
        observedAt: Date.parse(payload.observed_at),
        mantissa: BigInt(payload.value_mantissa),
        resolution: BigInt(payload.resolution_mantissa),
        exponent: payload.decimal_exponent,
        unit: payload.unit,
        decimal: decimalString(BigInt(payload.value_mantissa), payload.decimal_exponent),
        payload,
    });
}

function frozenState(values = {}) {
    return Object.freeze({
        transport: "idle",
        scaleActive: false,
        scaleValid: false,
        scaleReason: "inactive",
        scannerActive: false,
        ...values,
    });
}

/** Applies POS input rules to one authenticated browser Device Stream. */
export class InariDeviceInputAdapter {
    constructor({
        streams,
        scalePort = null,
        scannerPort = null,
        reconcile = async () => {},
        now = Date.now,
        timers = globalThis,
    } = {}) {
        if (!streams || typeof streams.open !== "function") {
            throw new TypeError("The input Adapter requires a browser Device Stream");
        }
        if (typeof timers.setTimeout !== "function" || typeof timers.clearTimeout !== "function") {
            throw new TypeError("The input Adapter requires browser timers");
        }
        this.streams = streams;
        this.scalePort = scalePort;
        this.scannerPort = scannerPort;
        this.reconcile = reconcile;
        this.now = now;
        this.timers = timers;
        this.session = null;
        this.state = frozenState();
        this.listeners = new Set();
        this.scaleBinding = null;
        this.scannerBinding = null;
        this.scaleCandidate = null;
        this.scaleReading = null;
        this.lastConsumedScaleReading = null;
        this.lastScaleSequence = new Map();
        this.lastBarcodeSequence = new Map();
        this.staleTimer = null;
    }

    snapshot() {
        return this.state;
    }

    subscribe(listener) {
        this.listeners.add(listener);
        listener(this.state);
        return () => this.listeners.delete(listener);
    }

    async start({ scaleBinding = null, scannerBinding = null } = {}) {
        if (this.session) throw new TypeError("The input Adapter is already running");
        this.scaleBinding = scaleBinding?.authoritative === true ? scaleBinding : null;
        this.scannerBinding = scannerBinding?.authoritative === true ? scannerBinding : null;
        const scale = bindingSelection(this.scaleBinding, "scale", (payload) =>
            this.acceptScale(payload),
        );
        const scanner = bindingSelection(this.scannerBinding, "scanner", (payload) =>
            this.acceptBarcode(payload),
        );
        if (!scale && !scanner) {
            throw new TypeError("The input Adapter requires a Scale or Scanner Binding");
        }
        this.setState({ scannerActive: Boolean(scanner) });
        this.session = await this.streams.open({
            scale,
            scanner,
            scaleActive: false,
            reconcile: async (barrier) => {
                this.scaleCandidate = null;
                this.invalidateScale("reconciling");
                await this.reconcile(barrier);
            },
            onState: (state) => this.onTransportState(state),
        });
        return this;
    }

    async setScaleActive(active) {
        if (!this.scaleBinding || !this.scalePort || !this.session) {
            throw new TypeError("This input Adapter has no active Certified Scale");
        }
        const next = active === true;
        if (!next) {
            this.scaleCandidate = null;
            this.invalidateScale("inactive");
        }
        this.setState({ scaleActive: next });
        const state = await this.session.setScaleActive(next);
        this.onTransportState(state);
        return this.state;
    }

    consumeScaleReading() {
        if (!this.scaleReading || !this.state.scaleValid) return null;
        this.lastConsumedScaleReading = this.scaleReading;
        this.scaleCandidate = null;
        return this.scaleReading;
    }

    async stop() {
        this.clearStaleTimer();
        const session = this.session;
        this.session = null;
        this.scaleCandidate = null;
        this.invalidateScale("closed");
        this.setState({ transport: "closed", scannerActive: false });
        await session?.close();
    }

    async acceptScale(payload) {
        const reading = exactReading(payload);
        const cursorKey = `${reading.agentBootId}|${reading.deviceId}`;
        const priorSequence = this.lastScaleSequence.get(cursorKey) ?? -1;
        if (reading.sequence <= priorSequence) return;
        this.lastScaleSequence.set(cursorKey, reading.sequence);

        const age = this.now() - reading.observedAt;
        const invalidReason =
            age > MAX_SCALE_AGE_MS || age < -MAX_FUTURE_SKEW_MS
                ? "stale"
                : payload.stable !== true
                  ? "unstable"
                  : payload.range_state !== "valid"
                    ? payload.range_state
                    : !this.scalePort.isUnitCompatible(reading.unit)
                      ? "unit_incompatible"
                      : reading.mantissa <= 0n
                        ? "non_positive"
                        : sameExactValue(reading, this.lastConsumedScaleReading)
                          ? "weight_unchanged"
                          : null;
        if (invalidReason) {
            this.scaleCandidate = null;
            this.invalidateScale(invalidReason);
            return;
        }

        if (
            !this.scaleCandidate ||
            this.scaleCandidate.agentBootId !== reading.agentBootId ||
            this.scaleCandidate.deviceId !== reading.deviceId ||
            this.scaleCandidate.unit !== reading.unit ||
            !distanceWithinResolution(this.scaleCandidate, reading)
        ) {
            this.scaleCandidate = reading;
            this.invalidateScale("stabilizing");
            return;
        }

        this.scaleCandidate = reading;
        this.scaleReading = reading;
        this.setState({ scaleValid: true, scaleReason: null });
        await this.scalePort.accept(reading);
        this.armStaleTimer(reading);
    }

    async acceptBarcode(payload) {
        if (!this.scannerPort) return;
        const cursorKey = `${payload.agent_boot_id}|${payload.device_id}`;
        const priorSequence = this.lastBarcodeSequence.get(cursorKey) ?? -1;
        if (payload.sequence <= priorSequence) return;
        await this.scannerPort.scan(payload.value);
        this.lastBarcodeSequence.set(cursorKey, payload.sequence);
    }

    onTransportState(transportState) {
        if (!transportState) return;
        const wasScaleActive = this.state.scaleActive;
        const scaleActive = transportState.scaleActive === true && wasScaleActive;
        this.setState({ transport: transportState.name, scaleActive });
        if (wasScaleActive && transportState.name !== "connected") {
            this.scaleCandidate = null;
            this.invalidateScale(transportState.name);
        }
        this.scannerPort?.setState?.(transportState);
    }

    invalidateScale(reason) {
        this.clearStaleTimer();
        this.scaleReading = null;
        this.setState({ scaleValid: false, scaleReason: reason });
        this.scalePort?.invalidate(reason);
    }

    armStaleTimer(reading) {
        this.clearStaleTimer();
        const delay = Math.max(0, reading.observedAt + MAX_SCALE_AGE_MS - this.now());
        this.staleTimer = this.timers.setTimeout(() => {
            this.staleTimer = null;
            if (this.scaleReading === reading) {
                this.scaleCandidate = null;
                this.invalidateScale("stale");
            }
        }, delay);
    }

    clearStaleTimer() {
        if (this.staleTimer !== null) this.timers.clearTimeout(this.staleTimer);
        this.staleTimer = null;
    }

    setState(changes) {
        this.state = frozenState({ ...this.state, ...changes });
        for (const listener of this.listeners) listener(this.state);
    }
}

export const deviceInputTiming = Object.freeze({
    maxScaleAgeMs: MAX_SCALE_AGE_MS,
    maxFutureSkewMs: MAX_FUTURE_SKEW_MS,
});
