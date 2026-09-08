/** @odoo-module */

import { canonicalJson } from "./agent_client";

const MAX_EVENT_BYTES = 64 * 1024;
const MAX_LINE_BYTES = 64 * 1024;
const SAFE_SEQUENCE = Number.MAX_SAFE_INTEGER;
const STABLE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;
const HOLDER_IDENTIFIER = /^[A-Za-z0-9_-]{16,128}$/;
const SIGNED_INTEGER = /^-?(?:0|[1-9]\d*)$/;
const POSITIVE_INTEGER = /^(?:[1-9]\d*)$/;
const MIN_SIGNED_64 = -(2n ** 63n);
const MAX_SIGNED_64 = 2n ** 63n - 1n;
const MAX_POSITIVE_64 = 2n ** 63n - 1n;
const STREAM_KINDS = new Set([
    "ready",
    "scale_reading",
    "barcode",
    "heartbeat",
    "scale_lease_lost",
    "replay_unavailable",
    "lease_lost",
]);
const DATA_KINDS = new Set(["scale_reading", "barcode"]);

export class DeviceStreamProtocolError extends Error {
    constructor(code, message) {
        super(message);
        this.name = "DeviceStreamProtocolError";
        this.code = code;
        this.retryable = false;
    }
}

function fail(code, message) {
    throw new DeviceStreamProtocolError(code, message);
}

function plainObject(value, name) {
    if (!value || Object.getPrototypeOf(value) !== Object.prototype) {
        fail("stream_protocol_invalid", `${name} must be a JSON object`);
    }
    return value;
}

function stableIdentifier(value, name) {
    if (typeof value !== "string" || !STABLE_IDENTIFIER.test(value)) {
        fail("stream_identity_mismatch", `${name} is invalid`);
    }
    return value;
}

function safeSequence(value, name) {
    if (!Number.isSafeInteger(value) || value < 0 || value > SAFE_SEQUENCE) {
        fail("stream_sequence_invalid", `${name} is invalid`);
    }
    return value;
}

function exactKeys(value, expected, name) {
    const actual = Object.keys(value).toSorted();
    if (JSON.stringify(actual) !== JSON.stringify([...expected].toSorted())) {
        fail("stream_protocol_invalid", `${name} contains unexpected fields`);
    }
}

function base64UrlBytes(value, name) {
    if (
        typeof value !== "string" ||
        !value ||
        !/^[A-Za-z0-9_-]+$/.test(value) ||
        value.length % 4 === 1
    ) {
        fail("stream_signature_invalid", `${name} is not unpadded base64url`);
    }
    try {
        const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
        const binary = atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="));
        return Uint8Array.from(binary, (character) => character.charCodeAt(0));
    } catch {
        fail("stream_signature_invalid", `${name} is not valid base64url`);
    }
}

function utf8(bytes, name) {
    try {
        return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
        fail("stream_signature_invalid", `${name} is not valid UTF-8`);
    }
}

function parseJson(value, name) {
    try {
        return JSON.parse(value);
    } catch {
        fail("stream_protocol_invalid", `${name} is not valid JSON`);
    }
}

function freezeJson(value) {
    if (Array.isArray(value)) {
        for (const item of value) freezeJson(item);
        return Object.freeze(value);
    }
    if (value && Object.getPrototypeOf(value) === Object.prototype) {
        for (const item of Object.values(value)) freezeJson(item);
        return Object.freeze(value);
    }
    return value;
}

function sameJson(first, second) {
    return canonicalJson(first) === canonicalJson(second);
}

function validSigned64(value) {
    if (typeof value !== "string" || !SIGNED_INTEGER.test(value)) return false;
    const integer = BigInt(value);
    return integer >= MIN_SIGNED_64 && integer <= MAX_SIGNED_64;
}

function validPositive64(value) {
    if (typeof value !== "string" || !POSITIVE_INTEGER.test(value)) return false;
    return BigInt(value) <= MAX_POSITIVE_64;
}

async function deriveAgentId(jwk, cryptoApi) {
    const publicBytes = base64UrlBytes(jwk.x, "Agent signing key");
    if (publicBytes.byteLength !== 32) {
        fail("agent_identity_mismatch", "The Agent signing key has an invalid length");
    }
    const digest = new Uint8Array(await cryptoApi.subtle.digest("SHA-256", publicBytes));
    const hex = [...digest].map((value) => value.toString(16).padStart(2, "0")).join("");
    return `agt_${hex.slice(0, 24)}`;
}

/** Import the lease key only after it proves the paired Agent identity. */
export async function trustEventLease(lease, { pairedAgentId, holderId, selections, cryptoApi }) {
    plainObject(lease, "Event Lease");
    for (const name of [
        "lease_id",
        "subscription_id",
        "scope_digest",
        "agent_id",
        "agent_boot_id",
        "client_grant_id",
    ]) {
        stableIdentifier(lease[name], `Event Lease ${name}`);
    }
    if (typeof lease.holder_id !== "string" || !HOLDER_IDENTIFIER.test(lease.holder_id)) {
        fail("agent_identity_mismatch", "Event Lease holder_id is invalid");
    }
    safeSequence(lease.generation, "Event Lease generation");
    const issuedAt = Date.parse(lease.issued_at);
    const expiresAt = Date.parse(lease.expires_at);
    if (
        lease.ok !== true ||
        lease.holder_id !== holderId ||
        lease.agent_id !== pairedAgentId ||
        !sameJson(lease.selections, selections) ||
        !Number.isFinite(issuedAt) ||
        !Number.isFinite(expiresAt) ||
        expiresAt <= issuedAt
    ) {
        fail("agent_identity_mismatch", "The Event Lease does not match this browser scope");
    }
    if (
        !Number.isSafeInteger(lease.renew_after_ms) ||
        lease.renew_after_ms < 100 ||
        !Number.isSafeInteger(lease.heartbeat_interval_ms) ||
        lease.heartbeat_interval_ms < 100
    ) {
        fail("stream_protocol_invalid", "The Event Lease timing is invalid");
    }
    const jwk = plainObject(lease.signing_public_jwk, "Agent signing JWK");
    exactKeys(jwk, ["alg", "crv", "kid", "kty", "use", "x"], "Agent signing JWK");
    if (
        jwk.kty !== "OKP" ||
        jwk.crv !== "Ed25519" ||
        jwk.alg !== "EdDSA" ||
        jwk.use !== "sig" ||
        stableIdentifier(jwk.kid, "Agent signing key identity") !== jwk.kid ||
        (await deriveAgentId(jwk, cryptoApi)) !== pairedAgentId
    ) {
        fail("agent_identity_mismatch", "The signing key does not match the paired Agent");
    }
    let publicKey;
    try {
        publicKey = await cryptoApi.subtle.importKey("jwk", jwk, { name: "Ed25519" }, false, [
            "verify",
        ]);
    } catch {
        fail("stream_signature_invalid", "The Agent signing key cannot verify events");
    }
    return Object.freeze({
        lease: freezeJson(structuredClone(lease)),
        jwk: freezeJson(structuredClone(jwk)),
        publicKey,
    });
}

function assertIdentity(payload, lease) {
    const expected = {
        lease_id: lease.lease_id,
        subscription_id: lease.subscription_id,
        scope_digest: lease.scope_digest,
    };
    for (const [name, value] of Object.entries(expected)) {
        if (payload[name] !== value) {
            fail("stream_identity_mismatch", `The stream ${name} does not match its Event Lease`);
        }
    }
}

function assertDataIdentity(payload, lease, selection) {
    assertIdentity(payload, lease);
    const expected = {
        agent_id: lease.agent_id,
        agent_boot_id: lease.agent_boot_id,
        client_grant_id: lease.client_grant_id,
        device_id: selection.device_id,
        binding_revision_id: selection.binding_revision_id,
    };
    for (const [name, value] of Object.entries(expected)) {
        if (payload[name] !== value) {
            fail("stream_identity_mismatch", `The stream ${name} does not match its selection`);
        }
    }
    safeSequence(payload.agent_sequence, "Agent sequence");
    safeSequence(payload.sequence, "Device sequence");
    safeSequence(payload.monotonic_ms, "Device monotonic time");
    if (
        typeof payload.observed_at !== "string" ||
        !Number.isFinite(Date.parse(payload.observed_at))
    ) {
        fail("stream_protocol_invalid", "The Device observation time is invalid");
    }
}

function assertScale(payload, lease, selections) {
    const selection = selections.find((value) => value.kind === "scale");
    if (!selection) fail("stream_identity_mismatch", "The Event Lease did not select a scale");
    assertDataIdentity(payload, lease, selection);
    if (
        payload.certification_id !== selection.certification_id ||
        !validSigned64(payload.value_mantissa) ||
        !validPositive64(payload.resolution_mantissa) ||
        !Number.isInteger(payload.decimal_exponent) ||
        payload.decimal_exponent < -12 ||
        payload.decimal_exponent > 12 ||
        typeof payload.unit !== "string" ||
        payload.unit.length < 1 ||
        payload.unit.length > 32 ||
        typeof payload.stable !== "boolean" ||
        !["valid", "underload", "overload"].includes(payload.range_state)
    ) {
        fail("stream_protocol_invalid", "The Scale Reading is invalid");
    }
}

function assertBarcode(payload, lease, selections) {
    const selection = selections.find((value) => value.kind === "scanner");
    if (!selection) fail("stream_identity_mismatch", "The Event Lease did not select a scanner");
    assertDataIdentity(payload, lease, selection);
    if (
        typeof payload.symbology !== "string" ||
        !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(payload.symbology) ||
        typeof payload.value !== "string" ||
        !payload.value ||
        payload.value.includes("\0") ||
        new TextEncoder().encode(payload.value).byteLength > 4096
    ) {
        fail("stream_protocol_invalid", "The Barcode Event is invalid");
    }
}

function assertReady(payload, lease, jwk) {
    assertIdentity(payload, lease);
    const expected = {
        agent_id: lease.agent_id,
        agent_boot_id: lease.agent_boot_id,
        client_grant_id: lease.client_grant_id,
    };
    for (const [name, value] of Object.entries(expected)) {
        if (payload[name] !== value) {
            fail("stream_identity_mismatch", `The ready ${name} does not match its Event Lease`);
        }
    }
    safeSequence(payload.current_sequence, "Current stream sequence");
    safeSequence(payload.high_water_mark, "Reconciliation high-water mark");
    plainObject(payload.device_cursors, "Device cursors");
    for (const [deviceId, sequence] of Object.entries(payload.device_cursors)) {
        stableIdentifier(deviceId, "Device cursor identity");
        safeSequence(sequence, "Device cursor sequence");
    }
    if (!sameJson(payload.signing_public_jwk, jwk)) {
        fail("agent_identity_mismatch", "The ready signing key changed after lease acquisition");
    }
}

function assertControl(message, lease, jwk) {
    assertIdentity(message.payload, lease);
    if (message.kind === "ready") {
        assertReady(message.payload, lease, jwk);
        if (message.payload.current_sequence !== message.stream_sequence) {
            fail("stream_sequence_invalid", "The ready stream sequence is invalid");
        }
    } else if (message.kind === "heartbeat") {
        if (
            message.payload.agent_boot_id !== lease.agent_boot_id ||
            message.payload.subscription_id !== lease.subscription_id
        ) {
            fail("stream_identity_mismatch", "The heartbeat identity is invalid");
        }
    } else if (typeof message.payload.reason !== "string" || !message.payload.reason) {
        fail("stream_protocol_invalid", "The terminal stream reason is invalid");
    }
}

/** Verify one SSE frame and return only frozen, authenticated Device data. */
export async function verifyStreamFrame(frame, { lease, jwk, publicKey, selections, cryptoApi }) {
    if (!frame || typeof frame.data !== "string") {
        fail("stream_protocol_invalid", "The SSE frame has no data");
    }
    const message = plainObject(
        parseJson(frame.data, "Device Stream message"),
        "Device Stream message",
    );
    exactKeys(
        message,
        [
            "contract_major",
            "kind",
            "stream_sequence",
            "generation",
            "occurred_at",
            "payload",
            "signer_key_id",
            "signature",
        ],
        "Device Stream message",
    );
    if (
        message.contract_major !== 1 ||
        !STREAM_KINDS.has(message.kind) ||
        frame.event !== message.kind ||
        message.generation !== lease.generation ||
        message.signer_key_id !== jwk.kid ||
        typeof message.signature !== "string" ||
        !message.signature ||
        typeof message.occurred_at !== "string" ||
        !Number.isFinite(Date.parse(message.occurred_at))
    ) {
        fail("stream_identity_mismatch", "The Device Stream message identity is invalid");
    }
    safeSequence(message.stream_sequence, "Stream sequence");
    plainObject(message.payload, "Device Stream payload");
    if (DATA_KINDS.has(message.kind)) {
        if (!/^(?:0|[1-9]\d*)$/.test(frame.id || "")) {
            fail("stream_sequence_invalid", "A data event requires one SSE identity");
        }
        const eventId = Number(frame.id);
        safeSequence(eventId, "SSE identity");
        if (eventId !== message.stream_sequence) {
            fail("stream_sequence_invalid", "The SSE and message sequences do not match");
        }
    } else if (frame.id !== null) {
        fail("stream_sequence_invalid", "A control event cannot change the SSE identity");
    }

    const parts = message.signature.split(".");
    if (parts.length !== 3) fail("stream_signature_invalid", "The event JWS is malformed");
    const [protectedPart, payloadPart, signaturePart] = parts;
    const header = plainObject(
        parseJson(utf8(base64UrlBytes(protectedPart, "JWS header"), "JWS header"), "JWS header"),
        "JWS header",
    );
    exactKeys(header, ["alg", "kid", "typ"], "JWS header");
    if (
        header.alg !== "EdDSA" ||
        header.typ !== "inari-agent-event+jws" ||
        header.kid !== message.signer_key_id
    ) {
        fail("stream_signature_invalid", "The event JWS header is invalid");
    }
    const unsigned = { ...message };
    delete unsigned.signature;
    const signedPayload = utf8(base64UrlBytes(payloadPart, "JWS payload"), "JWS payload");
    if (signedPayload !== canonicalJson(unsigned)) {
        fail("stream_signature_invalid", "The event JWS payload does not match the SSE message");
    }
    let valid = false;
    try {
        valid = await cryptoApi.subtle.verify(
            "Ed25519",
            publicKey,
            base64UrlBytes(signaturePart, "JWS signature"),
            new TextEncoder().encode(`${protectedPart}.${payloadPart}`),
        );
    } catch {
        fail("stream_signature_invalid", "The event JWS signature cannot be verified");
    }
    if (!valid) fail("stream_signature_invalid", "The event JWS signature is invalid");

    if (message.kind === "scale_reading") {
        assertScale(message.payload, lease, selections);
    } else if (message.kind === "barcode") {
        assertBarcode(message.payload, lease, selections);
    } else {
        assertControl(message, lease, jwk);
    }
    return freezeJson(message);
}

function parseLine(line, event) {
    if (!line || line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
        if (value.length > 64) fail("stream_protocol_invalid", "The SSE event name is too long");
        event.name = value;
    } else if (field === "id") {
        if (value.includes("\0") || value.length > 32) {
            fail("stream_protocol_invalid", "The SSE identity is invalid");
        }
        event.id = value;
    } else if (field === "data") {
        event.data.push(value);
        event.dataBytes += new TextEncoder().encode(value).byteLength;
        if (event.data.length > 1) event.dataBytes += 1;
        if (event.dataBytes > MAX_EVENT_BYTES) {
            fail("stream_protocol_invalid", "The SSE event exceeds its size limit");
        }
    }
}

function dispatchEvent(event) {
    if (!event.data.length) return null;
    const frame = Object.freeze({
        event: event.name || "message",
        id: event.id,
        data: event.data.join("\n"),
    });
    event.name = "";
    event.id = null;
    event.data = [];
    event.dataBytes = 0;
    return frame;
}

/** Parse a bounded fetch-SSE body across arbitrary byte chunks. */
async function* readSseFrames(body) {
    if (!body || typeof body.getReader !== "function") {
        fail("stream_protocol_invalid", "The Agent returned no SSE body");
    }
    const reader = body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    const event = { name: "", id: null, data: [], dataBytes: 0 };
    let buffer = "";
    try {
        while (true) {
            // The stream reader is the transport backpressure point.
            // oxlint-disable-next-line no-await-in-loop
            const { value, done } = await reader.read();
            try {
                buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            } catch {
                fail("stream_protocol_invalid", "The SSE stream contains invalid UTF-8");
            }
            if (
                !buffer.includes("\n") &&
                new TextEncoder().encode(buffer).byteLength > MAX_LINE_BYTES
            ) {
                fail("stream_protocol_invalid", "The SSE line exceeds its size limit");
            }
            let newline = buffer.indexOf("\n");
            while (newline >= 0) {
                let line = buffer.slice(0, newline);
                buffer = buffer.slice(newline + 1);
                if (line.endsWith("\r")) line = line.slice(0, -1);
                if (new TextEncoder().encode(line).byteLength > MAX_LINE_BYTES) {
                    fail("stream_protocol_invalid", "The SSE line exceeds its size limit");
                }
                if (line === "") {
                    const frame = dispatchEvent(event);
                    if (frame) yield frame;
                } else {
                    parseLine(line, event);
                }
                newline = buffer.indexOf("\n");
            }
            if (done) break;
        }
        if (buffer) {
            const line = buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer;
            if (new TextEncoder().encode(line).byteLength > MAX_LINE_BYTES) {
                fail("stream_protocol_invalid", "The SSE line exceeds its size limit");
            }
            parseLine(line, event);
        }
        const frame = dispatchEvent(event);
        if (frame) yield frame;
    } finally {
        reader.releaseLock();
    }
}

export { readSseFrames };

export const deviceStreamProtocolLimits = Object.freeze({
    maxEventBytes: MAX_EVENT_BYTES,
    maxLineBytes: MAX_LINE_BYTES,
});
