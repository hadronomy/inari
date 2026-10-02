import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
    ClientPairingManager,
    MemoryPairingStore,
} from "../inari_devices/static/src/client_pairing.js";

const NOW = Date.parse("2026-08-31T12:00:00Z");

function binding() {
    return {
        authoritative: true,
        database: "shop",
        company_id: "1",
        organization_id: "organization-1",
        site_id: "site-1",
        pos_configuration_id: "42",
        agent_id: "agent-1",
        browser_origin: "https://odoo.example",
        agent_endpoint: "https://agent.example",
        audience: "inari-agent",
        requested_permissions: ["device_work:receipt_image", "jobs:read"],
    };
}

function decodePart(value) {
    return JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
}

async function thumbprint(jwk) {
    const canonical = JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x });
    return Buffer.from(
        await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical)),
    ).toString("base64url");
}

async function verifyProof(proof) {
    const [headerPart, payloadPart, signaturePart] = proof.split(".");
    const header = decodePart(headerPart);
    const payload = decodePart(payloadPart);
    const key = await crypto.subtle.importKey("jwk", header.jwk, { name: "Ed25519" }, false, [
        "verify",
    ]);
    const valid = await crypto.subtle.verify(
        "Ed25519",
        key,
        Buffer.from(signaturePart, "base64url"),
        new TextEncoder().encode(`${headerPart}.${payloadPart}`),
    );
    assert.equal(valid, true);
    assert.deepEqual(Object.keys(header).toSorted(), ["alg", "jwk", "typ"]);
    return { header, payload };
}

describe("Odoo browser Client Pairing", () => {
    test("uses the canonical active Device permissions as part of its scope", () => {
        const drawerBinding = {
            ...binding(),
            requested_permissions: ["device_work:drawer", "jobs:read"],
        };
        const options = {
            posSessionId: 9,
            store: new MemoryPairingStore(),
            fetchApi: async () => assert.fail("construction must not contact the Agent"),
            browserOrigin: "https://odoo.example",
            rpc: async () => assert.fail("construction must not contact Odoo"),
        };

        const receipt = new ClientPairingManager({ ...options, binding: binding() });
        const drawer = new ClientPairingManager({ ...options, binding: drawerBinding });

        assert.notEqual(receipt.scopeKey, drawer.scopeKey);
        assert.throws(
            () =>
                new ClientPairingManager({
                    ...options,
                    binding: {
                        ...drawerBinding,
                        requested_permissions: ["jobs:read", "device_work:drawer"],
                    },
                }),
            /invalid pairing permissions/,
        );
    });

    test("pairs with a non-exportable key and restores the Client Grant", async () => {
        const attempts = new Map();
        const observed = [];
        let requestThumbprint;
        const expiresAt = new Date(NOW + 10 * 60_000).toISOString();
        const fetchApi = async (url, options) => {
            const target = new URL(url);
            const route = `${options.method} ${target.pathname}`;
            const attempt = (attempts.get(route) || 0) + 1;
            attempts.set(route, attempt);
            const proof = await verifyProof(options.headers.get("DPoP"));
            observed.push({ route, attempt, proof, options });
            requestThumbprint ||= await thumbprint(proof.header.jwk);
            if (attempt % 2 === 1) {
                return new Response(null, {
                    status: 401,
                    headers: {
                        "DPoP-Nonce": `nonce-${route}`,
                        "WWW-Authenticate": 'DPoP realm="inari", error="use_dpop_nonce"',
                    },
                });
            }
            const scope = {
                agent_id: "agent-1",
                browser_origin: "https://odoo.example",
                agent_endpoint: "https://agent.example",
                database: "shop",
                company_id: "1",
                organization_id: "organization-1",
                site_id: "site-1",
                pos_configuration_id: "42",
                audience: "inari-agent",
            };
            const pairingRequest = {
                request_id: "pairing_request-1",
                scope,
                browser_jwk_thumbprint: requestThumbprint,
                requested_permissions: ["device_work:receipt_image", "jobs:read"],
                session_nonce: "session_nonce-1",
                phrase: "amber-river-seven",
                approval_uri: "inari://pairing/pairing_request-1",
                created_at: new Date(NOW).toISOString(),
                expires_at: expiresAt,
                state: target.pathname.endsWith("/requests") ? "pending" : "approved",
            };
            if (target.pathname.endsWith("/admit")) {
                assert.equal(JSON.parse(options.body).assertion, "odoo.assertion.signed");
                return Response.json({
                    pairing: { pairing_id: "pairing-1" },
                    grant: {
                        grant_id: "grant-1",
                        authorization_digest: "authorization-1",
                    },
                    access_token: "access-token-1",
                    expires_at: new Date(NOW + 5 * 60_000).toISOString(),
                });
            }
            return Response.json(pairingRequest, {
                status: target.pathname.endsWith("/requests") ? 201 : 200,
            });
        };
        const rpcCalls = [];
        const store = new MemoryPairingStore();
        const manager = new ClientPairingManager({
            binding: binding(),
            posSessionId: 9,
            store,
            fetchApi,
            browserOrigin: "https://odoo.example",
            now: () => NOW,
            sleep: async () => {},
            rpc: async (route, values) => {
                rpcCalls.push({ route, values });
                return { assertion: "odoo.assertion.signed", expires_at: expiresAt };
            },
        });

        await manager.begin();

        assert.equal(manager.snapshot().name, "ready");
        assert.equal(await manager.getAccessToken(), "access-token-1");
        assert.equal(rpcCalls.length, 1);
        assert.equal(rpcCalls[0].route, "/inari_devices/pairing/v1/assertion");
        assert.equal(rpcCalls[0].values.pairing_request.state, "approved");
        const stored = await store.get(manager.scopeKey);
        assert.equal(stored.privateKey.extractable, false);
        assert.equal(stored.publicJwk.d, undefined);
        assert.equal(stored.accessToken, "access-token-1");
        assert.equal(
            observed.every(({ proof }) => proof.payload.ath === undefined),
            true,
        );
        assert.equal(observed[1].proof.payload.nonce, "nonce-POST /pairing/v1/requests");

        const restored = new ClientPairingManager({
            binding: binding(),
            posSessionId: 9,
            store,
            fetchApi,
            browserOrigin: "https://odoo.example",
            now: () => NOW,
            rpc: async () => assert.fail("restoration must not call Odoo"),
        });
        assert.equal(await restored.restore(), true);
        assert.equal(await restored.getAccessToken(), "access-token-1");

        const protectedProof = await restored.createProof({
            method: "POST",
            url: "https://agent.example/v1/device-work",
            accessToken: "access-token-1",
            nonce: "agent-nonce",
        });
        const { payload } = await verifyProof(protectedProof);
        assert.deepEqual(Object.keys(payload).toSorted(), [
            "ath",
            "htm",
            "htu",
            "iat",
            "jti",
            "nonce",
        ]);
        assert.equal(
            payload.ath,
            Buffer.from(
                await crypto.subtle.digest("SHA-256", new TextEncoder().encode("access-token-1")),
            ).toString("base64url"),
        );
    });
});
