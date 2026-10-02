/** @odoo-module */

import { InariAgentError } from "./agent_client";
import {
    BroadcastTransportHintChannel,
    IndexedDbTransportCandidateStore,
    MemoryTransportCandidateStore,
    transportCoordinationDigest,
} from "./device_stream_coordination";
import {
    DeviceStreamProtocolError,
    readSseFrames,
    trustEventLease,
    verifyStreamFrame,
} from "./device_stream_protocol";

const CANDIDATE_RENEW_MS = 2_000;
const DEFAULT_LEADER_RETRY_MS = 3_000;
const STREAM_IDLE_MS = 30_000;
const MIN_RECONNECT_MS = 250;
const MAX_RECONNECT_MS = 10_000;
const STABLE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;

function base64Url(bytes) {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function randomHolderId(cryptoApi) {
    return base64Url(cryptoApi.getRandomValues(new Uint8Array(24)));
}

function stableIdentifier(value, name) {
    if (typeof value !== "string" || !STABLE_IDENTIFIER.test(value)) {
        throw new TypeError(`${name} must be a stable identifier`);
    }
    return value;
}

function streamSelection(value, kind) {
    if (!value || typeof value.handler !== "function") {
        throw new TypeError(
            `${kind === "scale" ? "Scale" : "Scanner"} selection requires a handler`,
        );
    }
    const selection = {
        kind,
        device_id: stableIdentifier(value.device_id, "Device identity"),
        binding_revision_id: stableIdentifier(
            value.binding_revision_id,
            "Binding Revision identity",
        ),
    };
    if (kind === "scale") {
        selection.certification_id = stableIdentifier(
            value.certification_id,
            "Certification identity",
        );
    } else {
        selection.certification_id = null;
    }
    return Object.freeze(selection);
}

function streamRequest(value) {
    if (!value || typeof value.reconcile !== "function") {
        throw new TypeError("A Device Stream requires a reconciliation Adapter");
    }
    const scale = value.scale ? streamSelection(value.scale, "scale") : null;
    const scanner = value.scanner ? streamSelection(value.scanner, "scanner") : null;
    if (!scale && !scanner) {
        throw new TypeError("A Device Stream requires a scale or scanner selection");
    }
    if (scale && scanner && scale.device_id === scanner.device_id) {
        throw new TypeError("A Device Stream cannot select one Device twice");
    }
    return Object.freeze({
        scale,
        scanner,
        scaleHandler: value.scale?.handler || null,
        scannerHandler: value.scanner?.handler || null,
        reconcile: value.reconcile,
        onState: typeof value.onState === "function" ? value.onState : null,
        scaleActive: value.scaleActive === true,
    });
}

function frozenState(name, values = {}) {
    return Object.freeze({ name, ...values });
}

function normalizeError(error) {
    if (error instanceof DeviceStreamError) return error;
    if (error instanceof InariAgentError || error instanceof DeviceStreamProtocolError) {
        return new DeviceStreamError(error.code, error.message, {
            status: error.status ?? null,
            retryable: error.retryable === true,
            retryAfterMs: error.retryAfterMs ?? null,
            cause: error,
        });
    }
    if (error?.name === "AbortError") {
        return new DeviceStreamError("stream_aborted", "The Device Stream was closed.", {
            retryable: true,
            cause: error,
        });
    }
    return new DeviceStreamError(
        "agent_unavailable",
        error?.message || "The Inari Agent is unavailable.",
        { retryable: true, cause: error },
    );
}

function safeSequence(value, name) {
    if (!Number.isSafeInteger(value) || value < 0) {
        throw new DeviceStreamError("stream_protocol_invalid", `${name} is invalid.`);
    }
    return value;
}

function validateScaleLease(value, eventLease, scaleSelection) {
    const issuedAt = Date.parse(value?.issued_at);
    const expiresAt = Date.parse(value?.expires_at);
    if (
        !value ||
        value.ok !== true ||
        value.event_lease_id !== eventLease.lease_id ||
        typeof value.scale_lease_id !== "string" ||
        !STABLE_IDENTIFIER.test(value.scale_lease_id) ||
        typeof value.device_id !== "string" ||
        !STABLE_IDENTIFIER.test(value.device_id) ||
        typeof value.binding_revision_id !== "string" ||
        !STABLE_IDENTIFIER.test(value.binding_revision_id) ||
        !Number.isSafeInteger(value.generation) ||
        value.generation < 0 ||
        !Number.isSafeInteger(value.renew_after_ms) ||
        value.renew_after_ms < 100 ||
        value.device_id !== scaleSelection.device_id ||
        value.binding_revision_id !== scaleSelection.binding_revision_id ||
        !Number.isFinite(issuedAt) ||
        !Number.isFinite(expiresAt) ||
        expiresAt <= issuedAt
    ) {
        throw new DeviceStreamError(
            "stream_protocol_invalid",
            "The Agent returned an invalid Scale Lease.",
        );
    }
    return Object.freeze(structuredClone(value));
}

function sameEventLease(first, second) {
    return [
        "lease_id",
        "subscription_id",
        "holder_id",
        "generation",
        "scope_digest",
        "agent_id",
        "agent_boot_id",
    ].every((name) => first[name] === second[name]);
}

function sameScaleLease(first, second) {
    return [
        "scale_lease_id",
        "event_lease_id",
        "device_id",
        "binding_revision_id",
        "generation",
    ].every((name) => first[name] === second[name]);
}

export class DeviceStreamError extends Error {
    constructor(
        code,
        message,
        { status = null, retryable = false, retryAfterMs = null, cause = null } = {},
    ) {
        super(message, cause ? { cause } : undefined);
        this.name = "DeviceStreamError";
        this.code = code;
        this.status = status;
        this.retryable = retryable;
        this.retryAfterMs = retryAfterMs;
    }
}

/** Deep browser module for one paired Agent and POS Device Stream scope. */
export class BrowserDeviceStreams {
    constructor({
        client,
        pairedAgentId,
        coordinationScope,
        candidateStore = globalThis.indexedDB
            ? new IndexedDbTransportCandidateStore()
            : new MemoryTransportCandidateStore(),
        hintChannelFactory = (scopeDigest) => new BroadcastTransportHintChannel(scopeDigest),
        cryptoApi = globalThis.crypto,
        timers = globalThis,
        random = Math.random,
    } = {}) {
        if (
            !client ||
            typeof client.acquireEventLease !== "function" ||
            typeof client.openEventStream !== "function"
        ) {
            throw new TypeError("BrowserDeviceStreams requires a Local Agent stream Adapter");
        }
        stableIdentifier(pairedAgentId, "Paired Agent identity");
        if (!coordinationScope || !cryptoApi?.subtle || !cryptoApi?.getRandomValues) {
            throw new TypeError("BrowserDeviceStreams requires scope and WebCrypto");
        }
        if (typeof timers.setTimeout !== "function" || typeof timers.clearTimeout !== "function") {
            throw new TypeError("BrowserDeviceStreams requires browser timers");
        }
        this.client = client;
        this.pairedAgentId = pairedAgentId;
        this.coordinationScope = coordinationScope;
        this.candidateStore = candidateStore;
        this.hintChannelFactory = hintChannelFactory;
        this.cryptoApi = cryptoApi;
        this.timers = timers;
        this.random = random;
    }

    async open(request) {
        const normalized = streamRequest(request);
        const scopeDigest = await transportCoordinationDigest(
            this.coordinationScope,
            this.cryptoApi,
        );
        const session = new BrowserDeviceStreamSession({
            ...this,
            ...normalized,
            scopeDigest,
            hints: this.hintChannelFactory(scopeDigest),
            holderId: randomHolderId(this.cryptoApi),
        });
        try {
            await session.start();
            return session;
        } catch (error) {
            await session.close();
            throw error;
        }
    }
}

class BrowserDeviceStreamSession {
    constructor({
        client,
        pairedAgentId,
        candidateStore,
        cryptoApi,
        timers,
        random,
        scopeDigest,
        hints,
        holderId,
        scale,
        scanner,
        scaleHandler,
        scannerHandler,
        reconcile,
        onState,
        scaleActive,
    }) {
        Object.assign(this, {
            client,
            pairedAgentId,
            candidateStore,
            cryptoApi,
            timers,
            random,
            scopeDigest,
            hints,
            holderId,
            scale,
            scanner,
            scaleHandler,
            scannerHandler,
            reconcile,
            onState,
            scaleActive,
        });
        this.state = frozenState("idle", { scaleActive });
        this.listeners = new Set();
        this.eventLease = null;
        this.scaleLease = null;
        this.trust = null;
        this.localCandidate = false;
        this.closed = false;
        this.attempt = null;
        this.streamAbort = null;
        this.streamVersion = 0;
        this.streamAuthorizationVersion = 0;
        this.lastEventId = null;
        this.lastHighWaterMark = 0;
        this.lastDeviceCursors = Object.freeze({});
        this.acceptedBarcodes = new Map();
        this.reconnectAttempt = 0;
        this.leaderTimer = null;
        this.reconnectTimer = null;
        this.eventRenewTimer = null;
        this.scaleRenewTimer = null;
        this.candidateRenewTimer = null;
        this.idleTimer = null;
        this.idleExpiredVersion = null;
        this.stopHints = null;
        this.hasConnected = false;
    }

    snapshot() {
        return this.state;
    }

    subscribe(listener) {
        this.listeners.add(listener);
        listener(this.state);
        return () => this.listeners.delete(listener);
    }

    async start() {
        try {
            this.stopHints = this.hints.subscribe(() => {
                if (!this.eventLease && !this.closed && this.currentSelections().length) {
                    this.scheduleLeadership(0);
                }
            });
        } catch {
            this.stopHints = null;
        }
        if (this.currentSelections().length) await this.attemptLeadership();
        return this;
    }

    async setScaleActive(active) {
        if (!this.scale) throw new TypeError("This Device Stream has no scale selection");
        const next = active === true;
        if (this.scaleActive === next || this.closed) return this.state;
        this.scaleActive = next;
        await this.releaseLeadership({ announce: true });
        if (this.currentSelections().length) {
            await this.attemptLeadership();
        } else {
            this.setState("idle");
        }
        return this.state;
    }

    async close() {
        if (this.closed) return;
        this.closed = true;
        this.clearAllTimers();
        this.stopHints?.();
        this.stopHints = null;
        await this.releaseLeadership({ announce: true });
        try {
            this.hints.close();
        } catch {
            // Browser coordination is advisory. The Agent Lease is authoritative.
        }
        this.setState("closed");
    }

    currentSelections() {
        return [this.scanner, this.scaleActive ? this.scale : null].filter(Boolean);
    }

    setState(name, values = {}) {
        if (this.closed && name !== "closed") return;
        this.state = frozenState(name, { scaleActive: this.scaleActive, ...values });
        try {
            this.onState?.(this.state);
        } catch {
            // A state observer cannot change Device Stream ownership.
        }
        for (const listener of this.listeners) {
            try {
                listener(this.state);
            } catch {
                // State observers are isolated from the transport state machine.
            }
        }
    }

    scheduleLeadership(delayMs) {
        if (this.leaderTimer !== null || this.closed || this.eventLease) return;
        this.leaderTimer = this.timers.setTimeout(() => {
            this.leaderTimer = null;
            void this.attemptLeadership().catch((error) => this.fail(error));
        }, delayMs);
    }

    async attemptLeadership() {
        if (this.attempt) return this.attempt;
        this.attempt = this.attemptLeadershipOnce().finally(() => {
            this.attempt = null;
        });
        return this.attempt;
    }

    async attemptLeadershipOnce() {
        if (this.closed || this.eventLease || !this.currentSelections().length) return false;
        this.setState("candidate");
        let candidateClaimed = true;
        try {
            candidateClaimed = await this.candidateStore.claim(this.scopeDigest, this.holderId);
        } catch {
            // Continue to the Agent Lease when local browser coordination is unavailable.
        }
        if (!candidateClaimed) {
            this.setState("follower");
            this.scheduleLeadership(DEFAULT_LEADER_RETRY_MS);
            return false;
        }
        this.localCandidate = true;
        this.scheduleCandidateRenewal();
        this.publishHint("candidate_changed");
        const selections = this.currentSelections();
        try {
            const lease = await this.client.acquireEventLease({
                holder_id: this.holderId,
                selections,
            });
            this.eventLease = lease;
            this.trust = await trustEventLease(lease, {
                pairedAgentId: this.pairedAgentId,
                holderId: this.holderId,
                selections,
                cryptoApi: this.cryptoApi,
            });
            this.eventLease = this.trust.lease;
            if (this.scaleActive) {
                this.scaleLease = validateScaleLease(
                    await this.client.acquireScaleLease(this.eventLease),
                    this.eventLease,
                    this.scale,
                );
            }
            this.scheduleEventRenewal();
            this.scheduleScaleRenewal();
            await this.connect(this.hasConnected ? "successor" : "initial");
            return true;
        } catch (rawError) {
            const error = normalizeError(rawError);
            if (this.scaleActive && !this.scaleLease && error.code === "scale_in_use") {
                this.scaleActive = false;
                await this.releaseLeadership({ announce: true });
                this.setState("scale_unavailable", { error });
                if (this.currentSelections().length) this.scheduleLeadership(0);
                return false;
            }
            if (this.eventLease && this.scaleActive && !this.scaleLease) {
                await this.releaseLeadership({ announce: false });
                if (error.retryable) {
                    this.setState("reconnecting", { error });
                    this.scheduleLeadership(error.retryAfterMs ?? DEFAULT_LEADER_RETRY_MS);
                    return false;
                }
                this.setState("error", { error });
                throw error;
            }
            if (this.eventLease && error.retryable && error.code !== "transport_leader_lost") {
                this.streamAbort?.abort();
                this.setState("reconnecting", { error });
                this.scheduleReconnect();
                return false;
            }
            await this.releaseLeadership({ announce: false });
            if (error.code === "transport_leader_active") {
                this.setState("follower", { error });
                this.scheduleLeadership(error.retryAfterMs ?? DEFAULT_LEADER_RETRY_MS);
                return false;
            }
            if (error.retryable) {
                this.setState("reconnecting", { error });
                this.scheduleLeadership(error.retryAfterMs ?? DEFAULT_LEADER_RETRY_MS);
                return false;
            }
            this.setState("error", { error });
            throw error;
        }
    }

    async connect(reason) {
        if (!this.eventLease || this.closed) return;
        const version = ++this.streamVersion;
        this.idleExpiredVersion = null;
        this.streamAbort?.abort();
        const abort = new AbortController();
        this.streamAbort = abort;
        this.setState(reason === "initial" ? "connecting" : "reconnecting");
        const response = await this.client.openEventStream({
            eventLease: this.eventLease,
            scaleLease: this.scaleLease,
            lastEventId: this.lastEventId,
            signal: abort.signal,
        });
        if (!response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream")) {
            throw new DeviceStreamError(
                "stream_protocol_invalid",
                "The Agent returned an invalid Device Stream media type.",
            );
        }
        const frames = readSseFrames(response.body)[Symbol.asyncIterator]();
        this.armIdleTimer(abort, version);
        const first = await frames.next();
        this.armIdleTimer(abort, version);
        if (first.done) {
            throw new DeviceStreamError("stream_closed", "The Agent closed the Device Stream.", {
                retryable: true,
            });
        }
        const ready = await this.verify(first.value);
        if (ready.kind !== "ready") {
            throw new DeviceStreamError(
                "stream_protocol_invalid",
                "The first Device Stream event is not ready.",
            );
        }
        this.lastHighWaterMark = ready.payload.high_water_mark;
        this.lastDeviceCursors = ready.payload.device_cursors;
        this.setState("reconciling");
        await this.reconcileBarrier(reason, ready.payload);
        if (this.closed || version !== this.streamVersion) return;
        this.streamAuthorizationVersion = this.client.authorizationVersion || 0;
        this.reconnectAttempt = 0;
        this.hasConnected = true;
        this.setState("connected", {
            agentBootId: this.eventLease.agent_boot_id,
            generation: this.eventLease.generation,
        });
        void this.consume(frames, abort, version).catch((error) =>
            this.streamFailed(error, version),
        );
    }

    async consume(frames, abort, version) {
        while (!this.closed && version === this.streamVersion) {
            // The async iterator is the stream backpressure point.
            // oxlint-disable-next-line no-await-in-loop
            const next = await frames.next();
            if (next.done) {
                throw new DeviceStreamError(
                    "stream_closed",
                    "The Agent closed the Device Stream.",
                    { retryable: true },
                );
            }
            this.armIdleTimer(abort, version);
            // Event handlers and acknowledgements must stay ordered.
            // oxlint-disable-next-line no-await-in-loop
            const keepReading = await this.handleFrame(next.value);
            if (!keepReading) return;
        }
    }

    async handleFrame(frame) {
        const message = await this.verify(frame);
        if (message.kind === "ready") {
            throw new DeviceStreamError(
                "stream_protocol_invalid",
                "The Agent sent ready more than once on one connection.",
            );
        }
        if (message.kind === "heartbeat") return true;
        if (message.kind === "lease_lost") {
            const error = new DeviceStreamError(
                "transport_leader_lost",
                "The Transport Leader Lease is no longer current.",
                { retryable: true },
            );
            await this.loseLeadership(error);
            return false;
        }
        if (message.kind === "scale_lease_lost") {
            this.scaleActive = false;
            const error = new DeviceStreamError(
                "scale_lease_lost",
                "The Scale Lease is no longer current.",
            );
            await this.releaseLeadership({ announce: true });
            this.setState("scale_lease_lost", { error });
            if (this.currentSelections().length) this.scheduleLeadership(0);
            return false;
        }
        if (message.kind === "replay_unavailable") {
            const error = new DeviceStreamError(
                "replay_unavailable",
                "The Agent cannot replay every Barcode Event.",
            );
            await this.reconcileBarrier("replay_unavailable", {
                high_water_mark: this.lastHighWaterMark,
                device_cursors: this.lastDeviceCursors,
            });
            await this.releaseLeadership({ announce: true });
            this.setState("replay_unavailable", { error });
            return false;
        }

        const sequence = message.stream_sequence;
        if (this.lastEventId !== null && sequence > this.lastEventId + 1) {
            await this.reconcileBarrier("sequence_gap", {
                high_water_mark: message.payload.agent_sequence,
                device_cursors: this.lastDeviceCursors,
            });
        }
        if (this.lastEventId !== null && sequence <= this.lastEventId) return true;
        if (message.kind === "scale_reading") {
            if (this.scaleActive) await this.scaleHandler(message.payload);
            this.lastEventId = sequence;
            return true;
        }
        if (message.kind === "barcode") {
            const deviceId = message.payload.device_id;
            const deviceSequence = message.payload.sequence;
            if ((this.acceptedBarcodes.get(deviceId) ?? -1) < deviceSequence) {
                await this.scannerHandler(message.payload);
                this.acceptedBarcodes.set(deviceId, deviceSequence);
            }
            await this.client.acknowledgeBarcodeEvents(this.eventLease, [
                { device_id: deviceId, sequence: deviceSequence },
            ]);
            this.lastEventId = sequence;
            this.publishHint("reconcile_requested");
            return true;
        }
        throw new DeviceStreamError(
            "stream_protocol_invalid",
            "The Device Stream event kind is invalid.",
        );
    }

    verify(frame) {
        return verifyStreamFrame(frame, {
            ...this.trust,
            selections: this.currentSelections(),
            cryptoApi: this.cryptoApi,
        });
    }

    async reconcileBarrier(reason, payload) {
        const highWaterMark = safeSequence(
            payload.high_water_mark,
            "Reconciliation high-water mark",
        );
        try {
            await this.reconcile(
                Object.freeze({
                    reason,
                    agent_id: this.eventLease.agent_id,
                    agent_boot_id: this.eventLease.agent_boot_id,
                    subscription_id: this.eventLease.subscription_id,
                    high_water_mark: highWaterMark,
                    device_cursors: Object.freeze({ ...payload.device_cursors }),
                }),
            );
        } catch (error) {
            throw new DeviceStreamError(
                "reconciliation_failed",
                error?.message || "Device state reconciliation failed.",
                { retryable: true, cause: error },
            );
        }
    }

    streamFailed(rawError, version) {
        if (this.closed || version !== this.streamVersion || !this.eventLease) return;
        let error = normalizeError(rawError);
        if (error.code === "stream_aborted") {
            if (this.idleExpiredVersion !== version) return;
            this.idleExpiredVersion = null;
            error = new DeviceStreamError(
                "stream_idle",
                "The Agent sent no Device Stream data before the idle limit.",
                { retryable: true, cause: rawError },
            );
        }
        if (error.code === "transport_leader_lost" || error.code === "expired") {
            void this.loseLeadership(error);
            return;
        }
        if (!error.retryable) {
            void this.fail(error);
            return;
        }
        this.setState("reconnecting", { error });
        this.scheduleReconnect();
    }

    scheduleReconnect() {
        if (this.reconnectTimer !== null || !this.eventLease || this.closed) return;
        const ceiling = Math.min(MAX_RECONNECT_MS, MIN_RECONNECT_MS * 2 ** this.reconnectAttempt);
        this.reconnectAttempt += 1;
        const delay = Math.floor(this.random() * ceiling);
        this.reconnectTimer = this.timers.setTimeout(() => {
            this.reconnectTimer = null;
            void this.connect("reconnect").catch((error) =>
                this.streamFailed(error, this.streamVersion),
            );
        }, delay);
    }

    scheduleEventRenewal() {
        this.clearTimer("eventRenewTimer");
        if (!this.eventLease || this.closed) return;
        const leaseId = this.eventLease.lease_id;
        this.eventRenewTimer = this.timers.setTimeout(() => {
            this.eventRenewTimer = null;
            void this.renewEventLease(leaseId);
        }, this.eventLease.renew_after_ms);
    }

    async renewEventLease(leaseId) {
        if (this.closed || this.eventLease?.lease_id !== leaseId) return;
        try {
            const prior = this.eventLease;
            const renewed = await trustEventLease(await this.client.renewEventLease(prior), {
                pairedAgentId: this.pairedAgentId,
                holderId: this.holderId,
                selections: this.currentSelections(),
                cryptoApi: this.cryptoApi,
            });
            if (!sameEventLease(prior, renewed.lease)) {
                throw new DeviceStreamError(
                    "stream_identity_mismatch",
                    "Event Lease renewal changed an immutable identity.",
                );
            }
            this.eventLease = renewed.lease;
            this.trust = renewed;
            this.scheduleEventRenewal();
            if (
                this.streamAuthorizationVersion &&
                this.client.authorizationVersion !== this.streamAuthorizationVersion
            ) {
                this.streamAbort?.abort();
                this.setState("reconnecting");
                this.scheduleReconnect();
            }
        } catch (error) {
            await this.loseLeadership(normalizeError(error));
        }
    }

    scheduleScaleRenewal() {
        this.clearTimer("scaleRenewTimer");
        if (!this.scaleLease || this.closed) return;
        const leaseId = this.scaleLease.scale_lease_id;
        this.scaleRenewTimer = this.timers.setTimeout(() => {
            this.scaleRenewTimer = null;
            void this.renewScaleLease(leaseId);
        }, this.scaleLease.renew_after_ms);
    }

    async renewScaleLease(leaseId) {
        if (this.closed || this.scaleLease?.scale_lease_id !== leaseId) return;
        try {
            const prior = this.scaleLease;
            const renewed = validateScaleLease(
                await this.client.renewScaleLease(prior),
                this.eventLease,
                this.scale,
            );
            if (!sameScaleLease(prior, renewed)) {
                throw new DeviceStreamError(
                    "stream_identity_mismatch",
                    "Scale Lease renewal changed an immutable identity.",
                );
            }
            this.scaleLease = renewed;
            this.scheduleScaleRenewal();
        } catch (rawError) {
            const error = normalizeError(rawError);
            this.scaleActive = false;
            await this.releaseLeadership({ announce: true });
            this.setState("scale_lease_lost", { error });
            if (this.currentSelections().length) this.scheduleLeadership(0);
        }
    }

    scheduleCandidateRenewal() {
        this.clearTimer("candidateRenewTimer");
        if (!this.localCandidate || this.closed) return;
        this.candidateRenewTimer = this.timers.setTimeout(() => {
            this.candidateRenewTimer = null;
            void this.renewCandidate();
        }, CANDIDATE_RENEW_MS);
    }

    async renewCandidate() {
        if (!this.localCandidate || this.closed) return;
        try {
            if (!(await this.candidateStore.renew(this.scopeDigest, this.holderId))) {
                this.localCandidate = false;
                this.publishHint("candidate_changed");
            }
        } catch {
            this.localCandidate = false;
        } finally {
            this.scheduleCandidateRenewal();
        }
    }

    armIdleTimer(abort, version) {
        this.clearTimer("idleTimer");
        this.idleTimer = this.timers.setTimeout(() => {
            if (version === this.streamVersion && !this.closed) {
                this.idleExpiredVersion = version;
                abort.abort();
            }
        }, STREAM_IDLE_MS);
    }

    async loseLeadership(error) {
        await this.releaseLeadership({ announce: true });
        if (this.closed) return;
        this.setState("follower", { error });
        if (this.currentSelections().length) {
            this.scheduleLeadership(error.retryAfterMs ?? DEFAULT_LEADER_RETRY_MS);
        }
    }

    async fail(rawError) {
        const error = normalizeError(rawError);
        await this.releaseLeadership({ announce: true });
        if (!this.closed) this.setState("error", { error });
    }

    async releaseLeadership({ announce }) {
        this.streamVersion += 1;
        this.streamAbort?.abort();
        this.streamAbort = null;
        this.idleExpiredVersion = null;
        this.clearTimer("idleTimer");
        this.clearTimer("reconnectTimer");
        this.clearTimer("eventRenewTimer");
        this.clearTimer("scaleRenewTimer");
        const scaleLease = this.scaleLease;
        const eventLease = this.eventLease;
        this.scaleLease = null;
        this.eventLease = null;
        this.trust = null;
        this.lastEventId = null;
        this.lastHighWaterMark = 0;
        this.lastDeviceCursors = Object.freeze({});
        this.acceptedBarcodes.clear();
        if (scaleLease) {
            try {
                await this.client.releaseScaleLease(scaleLease);
            } catch {
                // Lease expiry makes release idempotent from the browser view.
            }
        }
        if (eventLease) {
            try {
                await this.client.releaseEventLease(eventLease);
            } catch {
                // A fenced or expired Event Lease is already unavailable.
            }
        }
        if (this.localCandidate) {
            this.localCandidate = false;
            this.clearTimer("candidateRenewTimer");
            try {
                await this.candidateStore.release(this.scopeDigest, this.holderId);
            } catch {
                // The local claim expires quickly and does not grant Agent authority.
            }
        }
        if (announce) this.publishHint("lease_released");
    }

    publishHint(kind) {
        try {
            this.hints.publish(kind);
        } catch {
            // Browser hints carry no authority and never block the Device Stream.
        }
    }

    clearTimer(name) {
        if (this[name] !== null) this.timers.clearTimeout(this[name]);
        this[name] = null;
    }

    clearAllTimers() {
        for (const name of [
            "leaderTimer",
            "reconnectTimer",
            "eventRenewTimer",
            "scaleRenewTimer",
            "candidateRenewTimer",
            "idleTimer",
        ]) {
            this.clearTimer(name);
        }
    }
}

export const browserDeviceStreamTiming = Object.freeze({
    candidateRenewMs: CANDIDATE_RENEW_MS,
    leaderRetryMs: DEFAULT_LEADER_RETRY_MS,
    streamIdleMs: STREAM_IDLE_MS,
    reconnectMinMs: MIN_RECONNECT_MS,
    reconnectMaxMs: MAX_RECONNECT_MS,
});
