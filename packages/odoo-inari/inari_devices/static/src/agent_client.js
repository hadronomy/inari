/** @odoo-module */

import { envelopeFor } from "./submission_context";

const DPOP_NONCE_BYTES = 24;
const MAX_RECONCILIATION_IDS = 100;
const STABLE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;
const HOLDER_IDENTIFIER = /^[A-Za-z0-9_-]{16,128}$/;
const DRAWER_REASONS = new Set(["payment", "manual_open"]);
const STREAM_KINDS = new Set(["scale", "scanner"]);

export class InariAgentError extends Error {
    constructor(code, message, { status = null, retryable = false, retryAfterMs = null } = {}) {
        super(message);
        this.name = "InariAgentError";
        this.code = code;
        this.status = status;
        this.retryable = retryable;
        this.retryAfterMs = retryAfterMs;
    }
}

function joinUrl(baseUrl, path) {
    return new URL(path.replace(/^\//, ""), `${baseUrl.replace(/\/$/, "")}/`).href;
}

function assertTrustedEndpoint(baseUrl) {
    const endpoint = new URL(baseUrl);
    if (
        endpoint.protocol !== "https:" ||
        endpoint.username ||
        endpoint.password ||
        endpoint.search ||
        endpoint.hash ||
        !["", "/"].includes(endpoint.pathname)
    ) {
        throw new TypeError("The Agent Endpoint must be an exact trusted HTTPS origin");
    }
}

function base64Url(bytes) {
    let binary = "";
    for (const byte of bytes) {
        binary += String.fromCharCode(byte);
    }
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function randomNonce(cryptoApi) {
    return base64Url(cryptoApi.getRandomValues(new Uint8Array(DPOP_NONCE_BYTES)));
}

function canonicalValue(value) {
    if (value === null || typeof value === "boolean" || typeof value === "string") {
        return JSON.stringify(value);
    }
    if (typeof value === "number") {
        if (!Number.isFinite(value)) {
            throw new TypeError("canonical JSON accepts only finite numbers");
        }
        return JSON.stringify(value);
    }
    if (Array.isArray(value)) {
        return `[${value.map(canonicalValue).join(",")}]`;
    }
    if (value && Object.getPrototypeOf(value) === Object.prototype) {
        return `{${Object.keys(value)
            .toSorted()
            .map((key) => `${JSON.stringify(key)}:${canonicalValue(value[key])}`)
            .join(",")}}`;
    }
    throw new TypeError("canonical JSON accepts only JSON objects and values");
}

export function canonicalJson(value) {
    return canonicalValue(value);
}

async function problem(response) {
    let detail = `Agent request failed with status ${response.status}.`;
    let code = "agent_request_failed";
    let retryable = response.status >= 500;
    let retryAfterMs = null;
    try {
        const body = await response.json();
        detail = body.detail || body.title || detail;
        code = body.error_code || code;
        retryable = body.retryable ?? retryable;
        const retryAfterSeconds = body.details?.retry_after_seconds;
        if (Number.isInteger(retryAfterSeconds) && retryAfterSeconds >= 0) {
            retryAfterMs = retryAfterSeconds * 1000;
        }
    } catch {
        // The bounded status message is enough when the response is not Problem Details.
    }
    return new InariAgentError(code, detail, {
        status: response.status,
        retryable,
        retryAfterMs,
    });
}

function isNonceChallenge(response) {
    return (
        response.status === 401 &&
        response.headers.get("WWW-Authenticate")?.includes('error="use_dpop_nonce"') &&
        Boolean(response.headers.get("DPoP-Nonce"))
    );
}

function drawerIntentPayload(intent) {
    if (
        !intent ||
        intent.contract_major !== 1 ||
        !STABLE_IDENTIFIER.test(intent.drawer_intent_id) ||
        !STABLE_IDENTIFIER.test(intent.binding_revision_id) ||
        !STABLE_IDENTIFIER.test(intent.device_id) ||
        !STABLE_IDENTIFIER.test(intent.pos_session_id) ||
        !Number.isSafeInteger(intent.action_sequence) ||
        intent.action_sequence < 1 ||
        !DRAWER_REASONS.has(intent.reason)
    ) {
        throw new TypeError("Drawer submission requires one complete Drawer Intent");
    }
    return Object.freeze({
        contract_major: 1,
        drawer_intent_id: intent.drawer_intent_id,
        binding_revision_id: intent.binding_revision_id,
        device_id: intent.device_id,
        pos_session_id: intent.pos_session_id,
        action_sequence: intent.action_sequence,
        reason: intent.reason,
    });
}

function reconciliationIds(values, label) {
    if (
        !Array.isArray(values) ||
        values.length < 1 ||
        values.length > MAX_RECONCILIATION_IDS ||
        values.some((value) => typeof value !== "string" || !STABLE_IDENTIFIER.test(value))
    ) {
        throw new TypeError(`${label} requires 1 to 100 stable identities`);
    }
    return [...new Set(values)];
}

function stableIdentifier(value, name) {
    if (typeof value !== "string" || !STABLE_IDENTIFIER.test(value)) {
        throw new TypeError(`${name} must be a stable identifier`);
    }
    return value;
}

function safeSequence(value, name) {
    if (!Number.isSafeInteger(value) || value < 0) {
        throw new TypeError(`${name} must be a JavaScript-safe sequence`);
    }
    return value;
}

function eventLeaseControl(lease) {
    return Object.freeze({
        contract_major: 1,
        lease_id: stableIdentifier(lease?.lease_id, "Event Lease identity"),
        generation: safeSequence(lease?.generation, "Event Lease generation"),
    });
}

function scaleLeaseControl(lease) {
    return Object.freeze({
        contract_major: 1,
        scale_lease_id: stableIdentifier(lease?.scale_lease_id, "Scale Lease identity"),
        generation: safeSequence(lease?.generation, "Scale Lease generation"),
    });
}

function streamSelections(values) {
    if (!Array.isArray(values) || values.length < 1 || values.length > 2) {
        throw new TypeError("A Device Stream requires one scale or scanner selection");
    }
    const kinds = new Set();
    const devices = new Set();
    return values.map((value) => {
        if (!value || !STREAM_KINDS.has(value.kind) || kinds.has(value.kind)) {
            throw new TypeError("A Device Stream cannot repeat or use an unknown kind");
        }
        const deviceId = stableIdentifier(value.device_id, "Device identity");
        if (devices.has(deviceId)) {
            throw new TypeError("A Device Stream cannot repeat a Device");
        }
        const selection = {
            kind: value.kind,
            device_id: deviceId,
            binding_revision_id: stableIdentifier(
                value.binding_revision_id,
                "Binding Revision identity",
            ),
        };
        if (value.kind === "scale") {
            selection.certification_id = stableIdentifier(
                value.certification_id,
                "Certification identity",
            );
        } else if (value.certification_id != null) {
            throw new TypeError("A scanner selection cannot contain a Certification identity");
        } else {
            selection.certification_id = null;
        }
        kinds.add(value.kind);
        devices.add(deviceId);
        return Object.freeze(selection);
    });
}

/** Protected local-Agent transport for replay-safe receipt admission. */
export class InariAgentClient {
    constructor({ baseUrl, credentials, fetchApi = globalThis.fetch, cryptoApi = crypto } = {}) {
        if (!baseUrl || typeof fetchApi !== "function") {
            throw new TypeError("InariAgentClient requires baseUrl and fetchApi");
        }
        assertTrustedEndpoint(baseUrl);
        this.baseUrl = baseUrl;
        this.credentials = credentials;
        this.fetchApi = fetchApi;
        this.cryptoApi = cryptoApi;
        this.authorizationVersion = 0;
        this.accessToken = null;
    }

    async protectedRequest(path, options = {}) {
        if (
            !this.credentials ||
            typeof this.credentials.getAccessToken !== "function" ||
            typeof this.credentials.createProof !== "function"
        ) {
            throw new InariAgentError(
                "pairing_required",
                "Pair this browser with the Inari Agent before printing.",
            );
        }
        const method = String(options.method || "GET").toUpperCase();
        const url = joinUrl(this.baseUrl, path);
        const accessToken = await this.credentials.getAccessToken();
        if (!accessToken) {
            throw new InariAgentError(
                "pairing_required",
                "Pair this browser with the Inari Agent before printing.",
            );
        }
        if (accessToken !== this.accessToken) {
            this.accessToken = accessToken;
            this.authorizationVersion += 1;
        }

        const send = async (nonce) => {
            const proof = await this.credentials.createProof({
                method,
                url,
                accessToken,
                nonce,
            });
            const headers = new Headers(options.headers || {});
            headers.set("Authorization", `DPoP ${accessToken}`);
            headers.set("DPoP", proof);
            const response = await this.fetchApi(url, {
                ...options,
                method,
                headers,
                cache: "no-store",
                credentials: "omit",
            });
            return response;
        };

        let response = await send(randomNonce(this.cryptoApi));
        if (isNonceChallenge(response)) {
            response = await send(response.headers.get("DPoP-Nonce"));
            if (isNonceChallenge(response)) {
                throw new InariAgentError(
                    "dpop_challenge_failed",
                    "The Agent did not accept the browser proof after one retry.",
                );
            }
        }
        if (!response.ok) {
            throw await problem(response);
        }
        return response;
    }

    async submit(context, jpeg) {
        if (!(jpeg instanceof Blob) || jpeg.type !== "image/jpeg") {
            throw new TypeError("receipt submission requires an image/jpeg Blob");
        }
        const boundary = `inari-${randomNonce(this.cryptoApi).toLowerCase()}`;
        // FormData adds a filename to JSON Blobs; the Agent requires a plain JSON part.
        const body = new Blob(
            [
                `--${boundary}\r\nContent-Disposition: form-data; name="envelope"\r\n` +
                    "Content-Type: application/json\r\n\r\n",
                canonicalJson(envelopeFor(context)),
                `\r\n--${boundary}\r\nContent-Disposition: form-data; name="document"\r\n` +
                    "Content-Type: image/jpeg\r\n\r\n",
                jpeg,
                `\r\n--${boundary}--\r\n`,
            ],
            { type: `multipart/form-data; boundary=${boundary}` },
        );
        const response = await this.protectedRequest("/v1/device-work", {
            method: "POST",
            headers: {
                "Idempotency-Key": context.print_intent_id,
                "Content-Type": body.type,
            },
            body,
        });
        return response.json();
    }

    async queryPrintJobs(printIntentIds) {
        const uniqueIds = reconciliationIds(printIntentIds, "Print Job reconciliation");
        const response = await this.protectedRequest("/v1/jobs/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson({ print_intent_ids: uniqueIds }),
        });
        return response.json();
    }

    async submitDrawerIntent(intent) {
        const payload = drawerIntentPayload(intent);
        const response = await this.protectedRequest("/v1/drawer-intents", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Idempotency-Key": payload.drawer_intent_id,
            },
            body: canonicalJson(payload),
        });
        return response.json();
    }

    async queryDrawerIntents(drawerIntentIds) {
        const uniqueIds = reconciliationIds(drawerIntentIds, "Drawer Intent reconciliation");
        const response = await this.protectedRequest("/v1/drawer-intents/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson({ drawer_intent_ids: uniqueIds }),
        });
        return response.json();
    }

    async acquireEventLease({ holder_id: holderId, selections }) {
        if (typeof holderId !== "string" || !HOLDER_IDENTIFIER.test(holderId)) {
            throw new TypeError("An Event Lease requires a random browser holder identity");
        }
        const response = await this.protectedRequest("/v1/events/lease", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson({
                contract_major: 1,
                holder_id: holderId,
                selections: streamSelections(selections),
            }),
        });
        return response.json();
    }

    async renewEventLease(lease) {
        const response = await this.protectedRequest("/v1/events/lease/renew", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson(eventLeaseControl(lease)),
        });
        return response.json();
    }

    async releaseEventLease(lease) {
        await this.protectedRequest("/v1/events/lease", {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson(eventLeaseControl(lease)),
        });
    }

    async acquireScaleLease(eventLease) {
        const lease = eventLeaseControl(eventLease);
        const response = await this.protectedRequest("/v1/events/scale-lease", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson({
                contract_major: 1,
                event_lease_id: lease.lease_id,
                event_generation: lease.generation,
            }),
        });
        return response.json();
    }

    async renewScaleLease(lease) {
        const response = await this.protectedRequest("/v1/events/scale-lease/renew", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson(scaleLeaseControl(lease)),
        });
        return response.json();
    }

    async releaseScaleLease(lease) {
        await this.protectedRequest("/v1/events/scale-lease", {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson(scaleLeaseControl(lease)),
        });
    }

    async openEventStream({ eventLease, scaleLease = null, lastEventId = null, signal } = {}) {
        const lease = eventLeaseControl(eventLease);
        const headers = new Headers({
            Accept: "text/event-stream",
            "X-Inari-Event-Lease": lease.lease_id,
            "X-Inari-Event-Subscription": stableIdentifier(
                eventLease?.subscription_id,
                "Subscription identity",
            ),
            "X-Inari-Event-Generation": String(lease.generation),
        });
        if (scaleLease) {
            headers.set(
                "X-Inari-Scale-Lease",
                stableIdentifier(scaleLease.scale_lease_id, "Scale Lease identity"),
            );
        }
        if (lastEventId !== null) {
            headers.set("Last-Event-ID", String(safeSequence(lastEventId, "Last Event identity")));
        }
        return this.protectedRequest("/v1/events", {
            method: "GET",
            headers,
            signal,
        });
    }

    async acknowledgeBarcodeEvents(eventLease, acknowledgements) {
        const lease = eventLeaseControl(eventLease);
        if (
            !Array.isArray(acknowledgements) ||
            acknowledgements.length < 1 ||
            acknowledgements.length > 16
        ) {
            throw new TypeError("Barcode acknowledgement requires 1 to 16 Devices");
        }
        const devices = new Set();
        const values = acknowledgements.map((value) => {
            const deviceId = stableIdentifier(value?.device_id, "Barcode Device identity");
            if (devices.has(deviceId)) {
                throw new TypeError("Barcode acknowledgement cannot repeat a Device");
            }
            devices.add(deviceId);
            return {
                device_id: deviceId,
                sequence: safeSequence(value.sequence, "Barcode Event sequence"),
            };
        });
        const response = await this.protectedRequest("/v1/events/ack", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: canonicalJson({
                ...lease,
                subscription_id: stableIdentifier(
                    eventLease?.subscription_id,
                    "Subscription identity",
                ),
                acknowledgements: values,
            }),
        });
        return response.json();
    }
}
