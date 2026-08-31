import { describe, expect, test } from "@odoo/hoot";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import {
    PosPrinterService,
    posPrinterService,
} from "@point_of_sale/app/services/pos_printer_service";

import { InariAgentClient, canonicalJson } from "../../../src/agent_client";
import { MemoryContextStore } from "../../../src/context_store";
import { InariDeviceService } from "../../../src/inari_device_service";
import { ReceiptQueue } from "../../../src/receipt_queue";
import { createSubmissionContext, envelopeFor } from "../../../src/submission_context";
import "../../../src/pos_printer_patch";

function context() {
    return createSubmissionContext({
        contract_major: 1,
        print_intent_id: "pi_v1_receipt_1",
        origin_submission_key: "order-1:customer_receipt:revision-1",
        origin: {
            pos_session_id: "session-1",
            offline_order_id: "order-1",
            server_order_id: null,
            document_kind: "customer_receipt",
            content_revision: "revision-1",
        },
        binding_revision_id: "binding-1",
        device_id: "printer-1",
        copy_ordinal: 1,
    });
}

function posPrinter(inariDevice, nativePrinter) {
    const service = Object.create(PosPrinterService.prototype);
    service.hardware_proxy = { printer: nativePrinter };
    service.renderer = { toHtml: async () => document.createElement("div") };
    service.state = { isPrinting: false };
    service.inariDevice = inariDevice;
    return service;
}

describe("Inari customer receipt printing", () => {
    test("printer service receives the deep Inari service at composition", () => {
        const inariDevice = {};
        const service = posPrinterService.start(
            {},
            {
                hardware_proxy: { printer: null },
                dialog: {},
                renderer: {},
                inari_device: inariDevice,
            },
        );

        expect(service.inariDevice).toBe(inariDevice);
    });

    test("context rejects authority fields and canonicalizes the exact envelope", () => {
        const submissionContext = context();
        expect(Object.isFrozen(submissionContext)).toBe(true);
        expect(Object.keys(submissionContext.origin)).toEqual([
            "pos_session_id",
            "offline_order_id",
            "server_order_id",
            "document_kind",
            "content_revision",
        ]);
        expect(() =>
            createSubmissionContext({
                ...submissionContext,
                actor_id: "operator-1",
            }),
        ).toThrow();
        expect(canonicalJson(envelopeFor(submissionContext))).toContain(
            '"operation":"receipt_image"',
        );
    });

    test("Agent client retries one RFC 9449 challenge with the same receipt identity", async () => {
        const fetches = [];
        const proofs = [];
        const credentials = {
            getAccessToken: async () => "access-token",
            createProof: async (values) => {
                proofs.push(values);
                return `proof-${proofs.length}`;
            },
        };
        const fetchApi = async (_url, options) => {
            fetches.push(options);
            if (fetches.length === 1) {
                return new Response('{"error_code":"trust_required"}', {
                    status: 401,
                    headers: {
                        "Content-Type": "application/problem+json",
                        "DPoP-Nonce": "agent-nonce",
                        "WWW-Authenticate": 'DPoP realm="inari", error="use_dpop_nonce"',
                    },
                });
            }
            return new Response(
                JSON.stringify({
                    state: "accepted",
                    print_intent_id: context().print_intent_id,
                    print_job_id: "job-1",
                }),
                { status: 202, headers: { "Content-Type": "application/json" } },
            );
        };
        const client = new InariAgentClient({
            baseUrl: "https://agent.example",
            credentials,
            fetchApi,
            cryptoApi: {
                getRandomValues(bytes) {
                    bytes.fill(7);
                    return bytes;
                },
            },
        });

        const result = await client.submit(context(), new Blob(["jpeg"], { type: "image/jpeg" }));

        expect(result.state).toBe("accepted");
        expect(fetches).toHaveLength(2);
        expect(proofs).toHaveLength(2);
        expect(proofs[1].nonce).toBe("agent-nonce");
        expect(fetches[0].headers.get("Idempotency-Key")).toBe(
            fetches[1].headers.get("Idempotency-Key"),
        );
        expect(fetches[0].body.get("envelope").type).toBe("application/json");
        expect(fetches[0].body.get("document").type).toBe("image/jpeg");
    });

    test("receipt queue keeps one immutable context through retry", async () => {
        const calls = [];
        const queue = new ReceiptQueue({
            contextStore: new MemoryContextStore(),
            submit: async (submissionContext) => {
                calls.push(submissionContext);
                if (calls.length === 1) {
                    throw new Error("offline");
                }
                return { state: "accepted", print_job_id: "job-1" };
            },
        });
        const original = context();
        const entry = await queue.enqueue(original, new Blob(["jpeg"], { type: "image/jpeg" }));

        await queue.retry(entry.key);

        expect(calls).toHaveLength(2);
        expect(calls[0]).toBe(original);
        expect(calls[1]).toBe(original);
        expect(queue.snapshot()[0].state).toBe("accepted");
    });

    test("active Inari receipts bypass the native printer on success and failure", async () => {
        let nativeCalls = 0;
        const nativePrinter = {
            printReceipt: async () => {
                nativeCalls += 1;
                return { successful: true };
            },
        };
        const plan = Object.freeze({ print_intent_id: "pi_v1_receipt_1" });
        const accepted = posPrinter(
            {
                prepareReceiptPrint: () => plan,
                printReceipt: async () => ({
                    accepted: true,
                    context: { print_intent_id: plan.print_intent_id },
                    print_job_id: "job-1",
                }),
            },
            nativePrinter,
        );
        const failed = posPrinter(
            {
                prepareReceiptPrint: () => plan,
                printReceipt: async () => ({ accepted: false, state: "failed" }),
            },
            nativePrinter,
        );

        const acceptedResult = await accepted.print(OrderReceipt, { order: {} });
        const failedResult = await failed.print(OrderReceipt, { order: {} });

        expect(acceptedResult.successful).toBe(true);
        expect(acceptedResult.inari).toBe(true);
        expect(failedResult).toBe(undefined);
        expect(nativeCalls).toBe(0);
    });

    test("inactive Inari leaves Odoo native receipt printing unchanged", async () => {
        let nativeCalls = 0;
        const service = posPrinter(
            {
                prepareReceiptPrint: () => null,
            },
            {
                printReceipt: async () => {
                    nativeCalls += 1;
                    return { successful: true };
                },
            },
        );

        const result = await service.print(OrderReceipt, { order: {} });

        expect(result.successful).toBe(true);
        expect(result.inari).toBe(undefined);
        expect(nativeCalls).toBe(1);
    });

    test("active binding without browser pairing stays authoritative and explains the fix", async () => {
        const notifications = [];
        const service = new InariDeviceService({
            notification: {
                add(message, options) {
                    notifications.push({ message, options });
                },
            },
            randomUUID: () => "00000000-0000-0000-0000-000000000001",
        });
        service.attachPos({
            config: {
                inari_receipt_binding: {
                    authoritative: true,
                    binding_revision_id: "binding-1",
                    device_id: "printer-1",
                    agent_endpoint: "https://agent.example",
                },
            },
            session: { id: 42 },
        });
        const plan = service.prepareReceiptPrint({ uuid: "order-1", nb_print: 0 });

        const result = await service.printReceipt(document.createElement("div"), plan);

        expect(result.accepted).toBe(false);
        expect(notifications).toHaveLength(1);
        expect(notifications[0].message).toContain("Pair this browser");
        expect(notifications[0].options.sticky).toBe(true);
    });
});
