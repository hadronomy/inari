/** @odoo-module */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

import { InariAgentClient, InariAgentError } from "./agent_client";
import { ClientPairingManager } from "./client_pairing";
import { ClientPairingDialog } from "./client_pairing_dialog";
import { IndexedDbContextStore } from "./context_store";
import { InariReceiptPrinter } from "./inari_printer";
import { createReceiptPlan } from "./submission_context";

const FAILURE_MESSAGES = Object.freeze({
    agent_endpoint_required: _t(
        "This POS has an Inari receipt binding, but its authenticated Agent Endpoint is missing.",
    ),
    pairing_required: _t(
        "This POS uses Inari for receipts. Pair this browser before you print another ticket.",
    ),
    receipt_failed: _t(
        "Inari did not accept this receipt. The ticket remains available in receipt recovery.",
    ),
});

function activeBinding(pos) {
    const binding = pos?.config?.inari_receipt_binding;
    return binding?.authoritative === true ? Object.freeze({ ...binding }) : null;
}

/** Deep POS service for the authoritative Inari receipt path. */
export class InariDeviceService {
    constructor({
        notification,
        dialog = null,
        rpc = null,
        credentials = null,
        pairingManagerFactory = (options) => new ClientPairingManager(options),
        clientFactory = (options) => new InariAgentClient(options),
        contextStoreFactory = () => new IndexedDbContextStore(),
        printerFactory = (options) => new InariReceiptPrinter(options),
        randomUUID = () => crypto.randomUUID(),
    } = {}) {
        this.notification = notification;
        this.dialog = dialog;
        this.rpc = rpc;
        this.credentials = credentials;
        this.pairingManagerFactory = pairingManagerFactory;
        this.clientFactory = clientFactory;
        this.contextStoreFactory = contextStoreFactory;
        this.printerFactory = printerFactory;
        this.randomUUID = randomUUID;
        this.pos = null;
        this.binding = null;
        this.client = null;
        this.printer = null;
        this.contextStore = null;
        this.pairing = null;
        this.lastResult = null;
    }

    async attachPos(pos) {
        this.pos = pos;
        this.binding = activeBinding(pos);
        this.pairing = null;
        if (this.binding?.agent_endpoint && this.rpc) {
            this.pairing = this.pairingManagerFactory({
                binding: this.binding,
                posSessionId: this.pos.session.id,
                rpc: this.rpc,
            });
            if (await this.pairing.restore()) {
                this.credentials = this.pairing;
            }
        }
        this.configureTransport();
    }

    setCredentials(credentials) {
        this.credentials = credentials;
        this.configureTransport();
    }

    configureTransport() {
        this.client = null;
        this.printer = null;
        if (!this.binding?.agent_endpoint || !this.credentials) {
            return;
        }
        this.contextStore ||= this.contextStoreFactory();
        this.client = this.clientFactory({
            baseUrl: this.binding.agent_endpoint,
            credentials: this.credentials,
        });
        this.printer = this.printerFactory({
            client: this.client,
            contextStore: this.contextStore,
        });
    }

    isReceiptAuthoritative() {
        return Boolean(this.binding);
    }

    prepareReceiptPrint(order) {
        if (!this.isReceiptAuthoritative()) {
            return null;
        }
        try {
            return createReceiptPlan({
                binding: this.binding,
                order,
                posSessionId: this.pos.session.id,
                randomUUID: this.randomUUID,
            });
        } catch (error) {
            return Object.freeze({ planningError: error });
        }
    }

    async printReceipt(element, plan) {
        if (plan?.planningError) {
            return this.fail("receipt_failed", plan.planningError);
        }
        if (!this.binding?.agent_endpoint) {
            return this.fail("agent_endpoint_required");
        }
        if ((!this.credentials || !this.printer) && !(await this.ensurePairing())) {
            return this.fail("pairing_required");
        }
        const result = await this.printer.printReceipt(element, plan);
        this.lastResult = result;
        if (!result.accepted) {
            const code =
                result.error instanceof InariAgentError ? result.error.code : "receipt_failed";
            return this.fail(code, result.error, result);
        }
        return result;
    }

    async ensurePairing() {
        if (!this.pairing) {
            return false;
        }
        const close = this.dialog?.add(ClientPairingDialog, {
            manager: this.pairing,
        });
        try {
            await this.pairing.begin();
            this.setCredentials(this.pairing);
            close?.();
            this.notification?.add(_t("Browser paired. Sending the ticket now."), {
                type: "success",
            });
            return true;
        } catch {
            return false;
        }
    }

    fail(code, error = null, result = null) {
        const normalizedCode = FAILURE_MESSAGES[code] ? code : "receipt_failed";
        const failure = result || {
            accepted: false,
            state: "failed",
            error,
        };
        this.lastResult = failure;
        this.notification?.add(FAILURE_MESSAGES[normalizedCode], {
            title: _t("Receipt not sent to printer"),
            type: "danger",
            sticky: true,
        });
        return failure;
    }
}

export const inariDeviceService = {
    dependencies: ["dialog", "notification", "rpc"],
    start(_env, { dialog, notification, rpc }) {
        return new InariDeviceService({ dialog, notification, rpc });
    },
};

registry.category("services").add("inari_device", inariDeviceService);
