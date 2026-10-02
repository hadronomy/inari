import { describe, expect, test } from "@odoo/hoot";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import {
    PosPrinterService,
    posPrinterService,
} from "@point_of_sale/app/services/pos_printer_service";
import { PosStore } from "@point_of_sale/app/services/pos_store";

import { InariAgentClient, InariAgentError, canonicalJson } from "../../src/agent_client";
import { InariDeviceService } from "../../src/inari_device_service";
import { PrintRecoveryCoordinator } from "../../src/print_recovery";
import { MemoryRecoveryStore } from "../../src/recovery_store";
import { createSubmissionContext, envelopeFor } from "../../src/submission_context";
import "../../src/pos_printer_patch";

function context() {
    return createSubmissionContext({
        contract_major: 1,
        print_intent_id: "pi_v1_receipt_1",
        origin_submission_key: "order-1:customer_receipt:revision-1",
        origin: {
            kind: "pos",
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
    test("repeated receipt clicks keep the pending physical-copy identity", async () => {
        let sequence = 0;
        const service = new InariDeviceService({ randomUUID: () => String(++sequence) });
        service.binding = { binding_revision_id: "binding-1", device_id: "printer-1" };
        service.pos = { session: { id: 42 } };
        const order = { uuid: "order-1", nb_print: 0 };

        const first = (await service.prepareReceiptPrint(order));
        const repeat = (await service.prepareReceiptPrint(order));

        expect(repeat.print_intent_id).toBe(first.print_intent_id);
        order.nb_print = 1;
        expect((await service.prepareReceiptPrint(order)).print_intent_id).not.toBe(first.print_intent_id);
    });

    test("receipt clicks reuse the journaled identity after POS reload", async () => {
        const original = context();
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore([
                {
                    key: original.print_intent_id,
                    context: original,
                    state: "pending_agent",
                },
            ]),
            clientForContext: async () => null,
        });
        await recovery.restore();
        const service = new InariDeviceService({ randomUUID: () => "new-intent" });
        service.recovery = recovery;
        service.binding = { binding_revision_id: "binding-1", device_id: "printer-1" };
        service.pos = { session: { id: "session-1" } };

        const plan = (await service.prepareReceiptPrint({ uuid: "order-1", nb_print: 0 }));

        expect(plan.print_intent_id).toBe(original.print_intent_id);
    });

    test("Inari service dependencies exist in the installed Odoo registry", () => {
        for (const dependency of inariDeviceService.dependencies) {
            expect(registry.category("services").contains(dependency)).toBe(true);
        }
    });
    test("printer service receives the deep Inari service at composition", () => {
        const inariDevice = { marker: "inari" };
        const service = posPrinterService.start(
            {},
            {
                hardware_proxy: { printer: null },
                dialog: {},
                renderer: {},
                inari_device: inariDevice,
            },
        );

        expect(service.inariDevice.marker).toBe("inari");
    });

    test("context rejects authority fields and canonicalizes the exact envelope", () => {
        const submissionContext = context();
        expect(Object.isFrozen(submissionContext)).toBe(true);
        expect(Object.keys(submissionContext.origin)).toEqual([
            "kind",
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
        expect(
            canonicalJson(envelopeFor(submissionContext)).includes('"operation":"receipt_image"'),
        ).toBe(true);
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
                    device_id: "printer-1",
                    state_version: 1,
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

    test("Agent client reconciles unique Print Intent IDs through the protected query", async () => {
        const fetches = [];
        const client = new InariAgentClient({
            baseUrl: "https://agent.example",
            credentials: {
                getAccessToken: async () => "access-token",
                createProof: async () => "proof",
            },
            fetchApi: async (url, options) => {
                fetches.push({ url, options });
                return new Response(
                    JSON.stringify({
                        jobs: [],
                        missing_print_intent_ids: ["intent-1"],
                        high_water_mark: 0,
                    }),
                    { status: 200, headers: { "Content-Type": "application/json" } },
                );
            },
            cryptoApi: {
                getRandomValues(bytes) {
                    bytes.fill(7);
                    return bytes;
                },
            },
        });

        const result = await client.queryPrintJobs(["intent-1", "intent-1"]);

        expect(result.missing_print_intent_ids).toEqual(["intent-1"]);
        expect(fetches).toHaveLength(1);
        expect(fetches[0].url).toBe("https://agent.example/v1/jobs/query");
        expect(fetches[0].options.headers.get("Content-Type")).toBe("application/json");
        expect(fetches[0].options.body).toBe('{"print_intent_ids":["intent-1"]}');
    });

    test("print recovery keeps one immutable context through retry", async () => {
        const calls = [];
        const client = {
            submit: async (submissionContext) => {
                calls.push(submissionContext);
                if (calls.length === 1) {
                    throw new InariAgentError("printer_offline", "Printer offline", {
                        status: 503,
                        retryable: true,
                    });
                }
                return {
                    state: "accepted",
                    print_intent_id: submissionContext.print_intent_id,
                    print_job_id: "job-1",
                    device_id: submissionContext.device_id,
                    state_version: 1,
                };
            },
        };
        const recovery = new PrintRecoveryCoordinator({
            store: new MemoryRecoveryStore(),
            clientForContext: async () => client,
        });
        await recovery.restore();
        const original = context();
        await recovery.enqueue({
            context: original,
            jpeg: new Blob(["jpeg"], { type: "image/jpeg" }),
            client,
        });

        await recovery.act(original.print_intent_id, "retry");

        expect(calls).toHaveLength(2);
        expect(calls[0]).toBe(original);
        expect(calls[1]).toBe(original);
        expect((await recovery.knownResult(original.print_intent_id)).state).toBe("accepted");
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

    test("preparation recovery updates Odoo once outside the active print attempt", async () => {
        let updates = 0;
        let syncs = 0;
        const order = {
            updateLastOrderChange() {
                updates += 1;
            },
        };
        const service = new InariDeviceService();
        service.recovery = { blocksPreparation: () => false };
        service.pos = {
            models: {
                "pos.order": { getBy: () => order },
                "pos.prep.display": [],
            },
            async syncAllOrders() {
                syncs += 1;
            },
        };
        service.blockPreparationAttempt("order-1", { tracked: false });
        expect(service.canAdvancePreparation("order-1")).toBe(false);
        service.beginPreparationAttempt("order-1");
        expect(service.canAdvancePreparation("order-1")).toBe(true);

        service.blockPreparationAttempt("order-1", { tracked: true });
        await service.onPreparationSettled({ offline_order_id: "order-1" });
        expect(updates).toBe(0);
        service.endPreparationAttempt("order-1");

        service.blockPreparationAttempt("order-1", { tracked: true });
        await service.onPreparationSettled({ offline_order_id: "order-1" });
        expect(updates).toBe(1);
        expect(syncs).toBe(1);
    });

    test("mixed preparation printers keep native retry separate from Inari recovery", async () => {
        const blockers = [];
        const dialogs = [];
        const inariPrinter = {
            config: {
                name: "Kitchen",
                product_categories_ids: [],
                inari_preparation_binding: { authoritative: true },
            },
        };
        const nativePrinter = {
            config: {
                name: "Bar",
                product_categories_ids: [],
            },
        };
        const store = Object.create(PosStore.prototype);
        store.unwatched = { printers: [inariPrinter, nativePrinter] };
        store.env = {
            services: {
                inari_device: {
                    blockPreparationAttempt(orderId, options) {
                        blockers.push({ orderId, options });
                    },
                },
            },
        };
        store.dialog = {
            add(component, props) {
                dialogs.push({ component, props });
            },
        };
        store.generateOrderChange = () => ({ orderData: {}, changes: {} });
        store.generateReceiptsDataToPrint = async () => [{}];
        store.printOrderChanges = async (_data, printer) =>
            printer === inariPrinter
                ? {
                      successful: false,
                      inari: true,
                      printIntentId: "pi_v1_preparation_1",
                  }
                : {
                      successful: false,
                      inari: false,
                      message: { body: "Paper out" },
                  };

        const printed = await PosStore.prototype.printChanges.call(store, { uuid: "order-1" }, [
            {},
        ]);

        expect(printed).toBe(false);
        expect(blockers).toEqual([{ orderId: "order-1", options: { tracked: true } }]);
        expect(dialogs).toHaveLength(1);
        expect(dialogs[0].props.message).toBe("Bar: Paper out");
    });

    test("active binding without browser pairing stays authoritative and explains the fix", async () => {
        const notifications = [];
        const service = new InariDeviceService({
            notification: {
                add(message, options) {
                    notifications.push({ message, options });
                },
            },
            recoveryStoreFactory: () => new MemoryRecoveryStore(),
            randomUUID: () => "00000000-0000-0000-0000-000000000001",
        });
        await service.attachPos({
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
        const plan = (await service.prepareReceiptPrint({ uuid: "order-1", nb_print: 0 }));

        const result = await service.printReceipt(document.createElement("div"), plan);

        expect(result.accepted).toBe(false);
        expect(notifications).toHaveLength(1);
        expect(notifications[0].options.sticky).toBe(true);
    });
});
