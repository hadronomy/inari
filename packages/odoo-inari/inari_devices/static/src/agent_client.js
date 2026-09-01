/** @odoo-module */

import { envelopeFor } from "./submission_context";

const DPOP_NONCE_BYTES = 24;
const MAX_RECONCILIATION_IDS = 100;
const STABLE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;
const DRAWER_REASONS = new Set(["payment", "manual_open"]);

export class InariAgentError extends Error {
    constructor(code, message, { status = null, retryable = false } = {}) {
        super(message);
        this.name = "InariAgentError";
        this.code = code;
        this.status = status;
        this.retryable = retryable;
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
    try {
        const body = await response.json();
        detail = body.detail || body.title || detail;
        code = body.error_code || code;
        retryable = body.retryable ?? retryable;
    } catch {
        // The bounded status message is enough when the response is not Problem Details.
    }
    return new InariAgentError(code, detail, {
        status: response.status,
        retryable,
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
    }

    async protectedRequest(path, options) {
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
        const envelope = new Blob([canonicalJson(envelopeFor(context))], {
            type: "application/json",
        });
        const form = new FormData();
        form.append("envelope", envelope, "envelope.json");
        form.append("document", jpeg, `${context.print_intent_id}.jpg`);
        const response = await this.protectedRequest("/v1/device-work", {
            method: "POST",
            headers: { "Idempotency-Key": context.print_intent_id },
            body: form,
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
}
