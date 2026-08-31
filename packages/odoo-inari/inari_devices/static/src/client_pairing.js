/** @odoo-module */

const DATABASE_NAME = "inari-client-trust";
const DATABASE_VERSION = 1;
const STORE_NAME = "pairings";
const PAIRING_PERMISSION = "receipt_image";
const TOKEN_RENEWAL_MARGIN_MS = 60_000;
const INITIAL_NONCE_BYTES = 24;

export class ClientPairingError extends Error {
    constructor(code, message, { status = null } = {}) {
        super(message);
        this.name = "ClientPairingError";
        this.code = code;
        this.status = status;
    }
}

function exactHttpsOrigin(value, name) {
    let url;
    try {
        url = new URL(value);
    } catch {
        throw new TypeError(`${name} must be an exact HTTPS origin`);
    }
    if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        url.pathname !== "/" ||
        url.search ||
        url.hash
    ) {
        throw new TypeError(`${name} must be an exact HTTPS origin`);
    }
    return url.origin;
}

function base64Url(bytes) {
    let binary = "";
    for (const byte of new Uint8Array(bytes)) {
        binary += String.fromCharCode(byte);
    }
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function utf8(value) {
    return new TextEncoder().encode(value);
}

function randomToken(cryptoApi, bytes = INITIAL_NONCE_BYTES) {
    return base64Url(cryptoApi.getRandomValues(new Uint8Array(bytes)));
}

function encodePart(value) {
    return base64Url(utf8(JSON.stringify(value)));
}

function joinUrl(origin, path) {
    return new URL(path.replace(/^\//, ""), `${origin}/`).href;
}

function isNonceChallenge(response) {
    return (
        response.status === 401 &&
        response.headers.get("WWW-Authenticate")?.includes('error="use_dpop_nonce"') &&
        Boolean(response.headers.get("DPoP-Nonce"))
    );
}

async function responseProblem(response) {
    let code = "pairing_failed";
    let message = `The Agent rejected pairing with status ${response.status}.`;
    try {
        const body = await response.json();
        code = body.error_code || code;
        message = body.detail || body.title || message;
    } catch {
        // The bounded status message is safe when the response is not Problem Details.
    }
    return new ClientPairingError(code, message, { status: response.status });
}

function frozenState(name, values = {}) {
    return Object.freeze({ name, ...values });
}

function requiredBinding(binding, browserOrigin) {
    const names = [
        "database",
        "company_id",
        "organization_id",
        "site_id",
        "pos_configuration_id",
        "agent_id",
        "agent_endpoint",
        "audience",
    ];
    if (!binding || names.some((name) => typeof binding[name] !== "string" || !binding[name])) {
        throw new TypeError("Client Pairing requires one complete POS receipt binding");
    }
    const normalizedOrigin = exactHttpsOrigin(browserOrigin, "Browser origin");
    const bindingOrigin = exactHttpsOrigin(binding.browser_origin, "Binding browser origin");
    if (normalizedOrigin !== bindingOrigin) {
        throw new TypeError("The browser origin does not match the POS receipt binding");
    }
    if (
        !Array.isArray(binding.requested_permissions) ||
        binding.requested_permissions.length !== 1 ||
        binding.requested_permissions[0] !== PAIRING_PERMISSION
    ) {
        throw new TypeError("The POS receipt binding has invalid pairing permissions");
    }
    return Object.freeze({
        ...binding,
        browser_origin: bindingOrigin,
        agent_endpoint: exactHttpsOrigin(binding.agent_endpoint, "Agent Endpoint"),
        requested_permissions: Object.freeze([...binding.requested_permissions]),
    });
}

function pairingScopeKey(binding) {
    return [
        "v1",
        binding.browser_origin,
        binding.agent_endpoint,
        binding.database,
        binding.company_id,
        binding.pos_configuration_id,
        binding.agent_id,
    ].join("|");
}

function openDatabase(indexedDBApi) {
    return new Promise((resolve, reject) => {
        const request = indexedDBApi.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(STORE_NAME)) {
                request.result.createObjectStore(STORE_NAME, { keyPath: "scopeKey" });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function idbRequest(request) {
    return new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result ?? null);
        request.onerror = () => reject(request.error);
    });
}

export class IndexedDbPairingStore {
    constructor({ indexedDBApi = globalThis.indexedDB } = {}) {
        if (!indexedDBApi) {
            throw new TypeError("IndexedDB is required for browser Client Pairing");
        }
        this.indexedDBApi = indexedDBApi;
    }

    async get(scopeKey) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            return await idbRequest(
                database.transaction(STORE_NAME).objectStore(STORE_NAME).get(scopeKey),
            );
        } finally {
            database.close();
        }
    }

    async put(record) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            await idbRequest(
                database.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).put(record),
            );
        } finally {
            database.close();
        }
    }

    async delete(scopeKey) {
        const database = await openDatabase(this.indexedDBApi);
        try {
            await idbRequest(
                database
                    .transaction(STORE_NAME, "readwrite")
                    .objectStore(STORE_NAME)
                    .delete(scopeKey),
            );
        } finally {
            database.close();
        }
    }
}

export class MemoryPairingStore {
    constructor() {
        this.records = new Map();
    }

    async get(scopeKey) {
        return this.records.get(scopeKey) || null;
    }

    async put(record) {
        this.records.set(record.scopeKey, record);
    }

    async delete(scopeKey) {
        this.records.delete(scopeKey);
    }
}

/** Own one browser key and the complete Client Pairing lifecycle for one POS scope. */
export class ClientPairingManager {
    constructor({
        binding,
        posSessionId,
        rpc,
        store = new IndexedDbPairingStore(),
        fetchApi = globalThis.fetch,
        cryptoApi = globalThis.crypto,
        browserOrigin = globalThis.location?.origin,
        now = Date.now,
        pollIntervalMs = 1_500,
        sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    } = {}) {
        if (!cryptoApi?.subtle || typeof fetchApi !== "function" || typeof rpc !== "function") {
            throw new TypeError("Client Pairing requires WebCrypto, fetch, and Odoo RPC");
        }
        if (!Number.isInteger(Number(posSessionId)) || Number(posSessionId) <= 0) {
            throw new TypeError("Client Pairing requires an active POS session");
        }
        this.binding = requiredBinding(binding, browserOrigin);
        this.posSessionId = String(posSessionId);
        this.rpc = rpc;
        this.store = store;
        this.fetchApi = fetchApi;
        this.cryptoApi = cryptoApi;
        this.now = now;
        this.pollIntervalMs = pollIntervalMs;
        this.sleep = sleep;
        this.scopeKey = pairingScopeKey(this.binding);
        this.record = null;
        this.state = frozenState("idle");
        this.listeners = new Set();
        this.operation = null;
        this.canceled = false;
    }

    snapshot() {
        return this.state;
    }

    subscribe(listener) {
        this.listeners.add(listener);
        listener(this.state);
        return () => this.listeners.delete(listener);
    }

    async restore() {
        this.record = await this.store.get(this.scopeKey);
        if (this.record?.privateKey && this.record?.publicJwk && this.record?.grantId) {
            this.setState("ready", {
                pairingId: this.record.pairingId,
                grantId: this.record.grantId,
            });
            return true;
        }
        this.setState("idle");
        return false;
    }

    async begin() {
        if (this.operation) {
            return this.operation;
        }
        this.canceled = false;
        this.operation = this.runPairing().finally(() => {
            this.operation = null;
        });
        return this.operation;
    }

    async cancel() {
        this.canceled = true;
        const requestId = this.state.requestId;
        if (requestId && this.record?.privateKey && this.record?.publicJwk) {
            try {
                await this.keyBoundRequest(
                    `/pairing/v1/requests/${encodeURIComponent(requestId)}/cancel`,
                    { method: "POST" },
                );
            } catch {
                // Local cancellation remains final for this browser.
            }
        }
        this.setState("canceled");
    }

    async getAccessToken() {
        if (!this.record) {
            await this.restore();
        }
        if (!this.record?.accessToken || !this.record?.grantId || !this.record?.pairingId) {
            throw new ClientPairingError(
                "pairing_required",
                "Pair this browser with the Inari Agent before printing.",
            );
        }
        if (Date.parse(this.record.expiresAt) > this.now() + TOKEN_RENEWAL_MARGIN_MS) {
            return this.record.accessToken;
        }
        return this.renew();
    }

    async createProof({ method, url, accessToken, nonce }) {
        if (!this.record?.privateKey || !this.record?.publicJwk) {
            throw new ClientPairingError("pairing_required", "The browser pairing key is missing.");
        }
        return this.signProof({
            method,
            url,
            nonce,
            accessToken,
            privateKey: this.record.privateKey,
            publicJwk: this.record.publicJwk,
        });
    }

    async runPairing() {
        try {
            const restored = await this.restore();
            if (restored && Date.parse(this.record.expiresAt) > this.now()) {
                return this;
            }
            this.setState("securing");
            await this.ensureBrowserKey();
            const pairingRequest = await this.keyBoundRequest("/pairing/v1/requests", {
                method: "POST",
                body: {
                    browser_jwk: this.record.publicJwk,
                    business: {
                        database: this.binding.database,
                        company_id: this.binding.company_id,
                        organization_id: this.binding.organization_id,
                        site_id: this.binding.site_id,
                        pos_configuration_id: this.binding.pos_configuration_id,
                    },
                    requested_permissions: [...this.binding.requested_permissions],
                },
            });
            await this.validatePairingRequest(pairingRequest);
            this.setState("awaiting_approval", {
                requestId: pairingRequest.request_id,
                phrase: pairingRequest.phrase,
                expiresAt: pairingRequest.expires_at,
            });
            const approved = await this.waitForApproval(pairingRequest);
            this.throwIfCanceled();
            this.setState("finalizing", {
                requestId: approved.request_id,
                phrase: approved.phrase,
                expiresAt: approved.expires_at,
            });
            const signed = await this.rpc("/inari_devices/pairing/v1/assertion", {
                pairing_request: this.assertionRequest(approved),
                pos_session_id: this.posSessionId,
            });
            if (!signed || typeof signed.assertion !== "string") {
                throw new ClientPairingError(
                    "assertion_failed",
                    "Odoo returned an invalid Pairing Assertion.",
                );
            }
            const admitted = await this.keyBoundRequest(
                `/pairing/v1/requests/${encodeURIComponent(approved.request_id)}/admit`,
                { method: "POST", body: { assertion: signed.assertion } },
            );
            this.record = {
                ...this.record,
                pairingId: admitted.pairing.pairing_id,
                grantId: admitted.grant.grant_id,
                authorizationDigest: admitted.grant.authorization_digest,
                accessToken: admitted.access_token,
                expiresAt: admitted.expires_at,
            };
            await this.store.put(this.record);
            this.setState("ready", {
                pairingId: this.record.pairingId,
                grantId: this.record.grantId,
            });
            return this;
        } catch (error) {
            if (this.canceled) {
                throw new ClientPairingError("pairing_canceled", "Browser pairing was canceled.");
            }
            const failure =
                error instanceof ClientPairingError
                    ? error
                    : new ClientPairingError(
                          "pairing_failed",
                          error?.message || "Browser pairing failed.",
                      );
            this.setState("failed", { error: failure });
            throw failure;
        }
    }

    async ensureBrowserKey() {
        const stored = await this.store.get(this.scopeKey);
        if (stored?.privateKey && stored?.publicJwk) {
            this.record = stored;
            return;
        }
        const keyPair = await this.cryptoApi.subtle.generateKey({ name: "Ed25519" }, false, [
            "sign",
            "verify",
        ]);
        const exported = await this.cryptoApi.subtle.exportKey("jwk", keyPair.publicKey);
        const publicJwk = Object.freeze({ kty: "OKP", crv: "Ed25519", x: exported.x });
        this.record = {
            scopeKey: this.scopeKey,
            privateKey: keyPair.privateKey,
            publicJwk,
        };
        await this.store.put(this.record);
    }

    async waitForApproval(initialRequest) {
        let current = initialRequest;
        while (current.state === "pending") {
            this.throwIfCanceled();
            if (Date.parse(current.expires_at) <= this.now()) {
                throw new ClientPairingError(
                    "pairing_expired",
                    "The Pairing Request expired. Start pairing again.",
                );
            }
            await this.sleep(this.pollIntervalMs);
            this.throwIfCanceled();
            current = await this.keyBoundRequest(
                `/pairing/v1/requests/${encodeURIComponent(current.request_id)}`,
                { method: "GET" },
            );
            await this.validatePairingRequest(current);
        }
        if (current.state === "approved") {
            return current;
        }
        const messages = {
            denied: "A Device Manager denied the Pairing Request.",
            expired: "The Pairing Request expired. Start pairing again.",
            canceled: "The Pairing Request was canceled.",
        };
        throw new ClientPairingError(
            `pairing_${current.state}`,
            messages[current.state] || "The Pairing Request cannot continue.",
        );
    }

    async renew() {
        try {
            const renewed = await this.keyBoundRequest("/pairing/v1/client-grants/renew", {
                method: "POST",
                body: {
                    pairing_id: this.record.pairingId,
                    grant_id: this.record.grantId,
                },
            });
            this.record = {
                ...this.record,
                grantId: renewed.grant.grant_id,
                authorizationDigest: renewed.grant.authorization_digest,
                accessToken: renewed.access_token,
                expiresAt: renewed.expires_at,
            };
            await this.store.put(this.record);
            return this.record.accessToken;
        } catch (error) {
            if (error instanceof ClientPairingError && [401, 403, 410].includes(error.status)) {
                await this.store.delete(this.scopeKey);
                this.record = null;
                this.setState("expired");
                throw new ClientPairingError(
                    "pairing_required",
                    "The Client Grant expired. Pair this browser again.",
                );
            }
            throw error;
        }
    }

    async keyBoundRequest(path, { method, body = undefined }) {
        const url = joinUrl(this.binding.agent_endpoint, path);
        const send = async (nonce) => {
            const proof = await this.signProof({
                method,
                url,
                nonce,
                privateKey: this.record.privateKey,
                publicJwk: this.record.publicJwk,
            });
            const headers = new Headers({ DPoP: proof });
            if (body !== undefined) {
                headers.set("Content-Type", "application/json");
            }
            return this.fetchApi(url, {
                method,
                headers,
                body: body === undefined ? undefined : JSON.stringify(body),
                credentials: "omit",
                cache: "no-store",
            });
        };
        let response = await send(randomToken(this.cryptoApi));
        if (isNonceChallenge(response)) {
            response = await send(response.headers.get("DPoP-Nonce"));
        }
        if (!response.ok) {
            throw await responseProblem(response);
        }
        return response.json();
    }

    async signProof({ method, url, nonce, privateKey, publicJwk, accessToken = null }) {
        if (!nonce || typeof nonce !== "string") {
            throw new TypeError("DPoP requires one Agent nonce");
        }
        const target = new URL(url);
        target.search = "";
        target.hash = "";
        const payload = {
            htm: String(method).toUpperCase(),
            htu: target.href,
            iat: Math.floor(this.now() / 1000),
            nonce,
            jti: randomToken(this.cryptoApi, 18),
        };
        if (accessToken !== null) {
            payload.ath = base64Url(
                await this.cryptoApi.subtle.digest("SHA-256", utf8(accessToken)),
            );
        }
        const encodedHeader = encodePart({
            typ: "dpop+jwt",
            alg: "Ed25519",
            jwk: publicJwk,
        });
        const encodedPayload = encodePart(payload);
        const signingInput = `${encodedHeader}.${encodedPayload}`;
        const signature = await this.cryptoApi.subtle.sign(
            "Ed25519",
            privateKey,
            utf8(signingInput),
        );
        return `${signingInput}.${base64Url(signature)}`;
    }

    async validatePairingRequest(pairingRequest) {
        const thumbprint = base64Url(
            await this.cryptoApi.subtle.digest(
                "SHA-256",
                utf8(
                    JSON.stringify({
                        crv: this.record.publicJwk.crv,
                        kty: this.record.publicJwk.kty,
                        x: this.record.publicJwk.x,
                    }),
                ),
            ),
        );
        const scope = pairingRequest?.scope;
        const expected = {
            agent_id: this.binding.agent_id,
            browser_origin: this.binding.browser_origin,
            agent_endpoint: this.binding.agent_endpoint,
            database: this.binding.database,
            company_id: this.binding.company_id,
            organization_id: this.binding.organization_id,
            site_id: this.binding.site_id,
            pos_configuration_id: this.binding.pos_configuration_id,
            audience: this.binding.audience,
        };
        if (
            !pairingRequest ||
            typeof pairingRequest.request_id !== "string" ||
            pairingRequest.browser_jwk_thumbprint !== thumbprint ||
            !scope ||
            Object.entries(expected).some(([name, value]) => scope[name] !== value) ||
            JSON.stringify(pairingRequest.requested_permissions) !==
                JSON.stringify(this.binding.requested_permissions)
        ) {
            throw new ClientPairingError(
                "pairing_scope_mismatch",
                "The Agent returned a Pairing Request for a different POS scope.",
            );
        }
    }

    assertionRequest(pairingRequest) {
        return {
            request_id: pairingRequest.request_id,
            agent_id: pairingRequest.scope.agent_id,
            browser_origin: pairingRequest.scope.browser_origin,
            agent_endpoint: pairingRequest.scope.agent_endpoint,
            database: pairingRequest.scope.database,
            company_id: pairingRequest.scope.company_id,
            organization_id: pairingRequest.scope.organization_id,
            site_id: pairingRequest.scope.site_id,
            pos_configuration_id: pairingRequest.scope.pos_configuration_id,
            audience: pairingRequest.scope.audience,
            browser_jwk_thumbprint: pairingRequest.browser_jwk_thumbprint,
            requested_permissions: pairingRequest.requested_permissions,
            session_nonce: pairingRequest.session_nonce,
            expires_at: pairingRequest.expires_at,
            state: pairingRequest.state,
        };
    }

    throwIfCanceled() {
        if (this.canceled) {
            throw new ClientPairingError("pairing_canceled", "Browser pairing was canceled.");
        }
    }

    setState(name, values = {}) {
        this.state = frozenState(name, values);
        for (const listener of this.listeners) {
            listener(this.state);
        }
    }
}
