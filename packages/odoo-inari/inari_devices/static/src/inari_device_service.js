/** @odoo-module */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

import { InariAgentClient, InariAgentError } from "./agent_client";
import { ClientPairingManager } from "./client_pairing";
import { ClientPairingDialog } from "./client_pairing_dialog";
import { IndexedDbContextStore } from "./context_store";
import { InariReceiptPrinter } from "./inari_printer";
import { PreparationPlanBook } from "./preparation_print";
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

function channelKey(binding) {
    return [
        binding.browser_origin,
        binding.agent_endpoint,
        binding.database,
        binding.company_id,
        binding.organization_id,
        binding.site_id,
        binding.pos_configuration_id,
        binding.agent_id,
        binding.audience,
        ...(binding.requested_permissions || []),
    ].join("|");
}

function printerKey(binding) {
    return `${channelKey(binding)}|${binding.binding_revision_id}|${binding.device_id}`;
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
        preparationPlanBook = null,
        randomUUID = () => crypto.randomUUID(),
    } = {}) {
        this.notification = notification;
        this.dialog = dialog;
        this.rpc = rpc;
        this.providedCredentials = credentials;
        this.pairingManagerFactory = pairingManagerFactory;
        this.clientFactory = clientFactory;
        this.contextStoreFactory = contextStoreFactory;
        this.printerFactory = printerFactory;
        this.randomUUID = randomUUID;
        this.preparationPlans =
            preparationPlanBook || new PreparationPlanBook({ randomUUID: this.randomUUID });
        this.pos = null;
        this.binding = null;
        this.contextStore = null;
        this.pairing = null;
        this.channels = new Map();
        this.printers = new Map();
        this.lastResult = null;
    }

    async attachPos(pos) {
        this.pos = pos;
        this.binding = activeBinding(pos);
        this.pairing = null;
        this.channels.clear();
        this.printers.clear();
        if (this.binding?.agent_endpoint && this.rpc && !this.providedCredentials) {
            const channel = this.channelFor(this.binding);
            this.pairing = channel.manager;
            channel.ready = await channel.manager.restore();
            channel.restored = true;
        }
    }

    setCredentials(credentials) {
        this.providedCredentials = credentials;
        this.printers.clear();
    }

    channelFor(binding) {
        const key = channelKey(binding);
        let channel = this.channels.get(key);
        if (!channel) {
            const manager = this.pairingManagerFactory({
                binding,
                posSessionId: this.pos.session.id,
                rpc: this.rpc,
            });
            channel = { manager, ready: false, restored: false };
            this.channels.set(key, channel);
        }
        return channel;
    }

    async credentialsFor(binding) {
        if (this.providedCredentials) {
            return this.providedCredentials;
        }
        if (!this.rpc) {
            return null;
        }
        const channel = this.channelFor(binding);
        if (!channel.restored) {
            channel.ready = await channel.manager.restore();
            channel.restored = true;
        }
        if (!channel.ready && !(await this.ensurePairing(binding))) {
            return null;
        }
        return channel.manager;
    }

    async printerFor(binding) {
        const credentials = await this.credentialsFor(binding);
        if (!credentials) {
            return null;
        }
        const key = printerKey(binding);
        const existing = this.printers.get(key);
        if (existing) {
            return existing;
        }
        this.contextStore ||= this.contextStoreFactory();
        const client = this.clientFactory({
            baseUrl: binding.agent_endpoint,
            credentials,
        });
        const printer = this.printerFactory({
            client,
            contextStore: this.contextStore,
        });
        this.printers.set(key, printer);
        return printer;
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
        const printer = await this.printerFor(this.binding);
        if (!printer) {
            return this.fail("pairing_required");
        }
        const result = await printer.printReceipt(element, plan);
        this.lastResult = result;
        if (!result.accepted) {
            const code =
                result.error instanceof InariAgentError ? result.error.code : "receipt_failed";
            return this.fail(code, result.error, result);
        }
        return result;
    }

    async preparePreparationPrint(binding, source) {
        try {
            return await this.preparationPlans.plan({
                binding,
                source,
                posSessionId: this.pos.session.id,
            });
        } catch (error) {
            return Object.freeze({ planningError: error });
        }
    }

    async printPreparation(element, plan, binding, source, printerName) {
        let result;
        if (plan?.planningError) {
            result = { accepted: false, state: "failed", error: plan.planningError };
        } else if (!binding?.agent_endpoint) {
            result = { accepted: false, state: "failed" };
        } else {
            const printer = await this.printerFor(binding);
            result = printer
                ? await printer.printReceipt(element, plan)
                : { accepted: false, state: "failed" };
        }
        if (!plan?.planningError) {
            this.preparationPlans.settle({ binding, source, result });
        }
        if (result.accepted) {
            return {
                successful: true,
                inari: true,
                printIntentId: result.context.print_intent_id,
                printJobId: result.print_job_id,
            };
        }
        return {
            successful: false,
            canRetry: true,
            message: {
                title: _t("Preparation ticket not sent"),
                body: _t(
                    "Inari did not accept the ticket for %s. Check the printer and try again.",
                    printerName,
                ),
            },
        };
    }

    async ensurePairing(binding = this.binding) {
        if (!binding || !this.rpc) {
            return false;
        }
        const channel = this.channelFor(binding);
        this.pairing = channel.manager;
        const close = this.dialog?.add(ClientPairingDialog, {
            manager: channel.manager,
        });
        try {
            await channel.manager.begin();
            channel.ready = true;
            channel.restored = true;
            close?.();
            this.notification?.add(_t("Browser paired. Sending the print job now."), {
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
