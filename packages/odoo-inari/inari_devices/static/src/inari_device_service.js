/** @odoo-module */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

import { InariAgentClient, InariAgentError } from "./agent_client";
import { ClientPairingManager } from "./client_pairing";
import { ClientPairingDialog } from "./client_pairing_dialog";
import { InariReceiptPrinter } from "./inari_printer";
import { PreparationPlanBook } from "./preparation_print";
import { PrintRecoveryCoordinator } from "./print_recovery";
import { InariRecoveryDialog } from "./recovery_dialog";
import { IndexedDbRecoveryStore } from "./recovery_store";
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

function recoveryChannel(binding) {
    return Object.freeze(Object.fromEntries([
        "browser_origin", "agent_endpoint", "database", "company_id", "organization_id",
        "site_id", "pos_configuration_id", "agent_id", "audience", "requested_permissions",
    ].map((key) => [key, binding[key]])));
}

function recoveryDatabaseName(bindings, posSessionId) {
    const scopes = new Set(
        bindings
            .filter(
                (binding) => binding.database && binding.company_id && binding.pos_configuration_id,
            )
            .map((binding) =>
                [binding.database, binding.company_id, binding.pos_configuration_id]
                    .map((value) => encodeURIComponent(String(value)))
                    .join(":"),
            ),
    );
    if (scopes.size > 1) {
        throw new TypeError("Inari POS bindings must share one Odoo recovery scope");
    }
    const [scope] = scopes;
    return scope
        ? `inari-print-recovery:${scope}`
        : `inari-print-recovery:session:${encodeURIComponent(String(posSessionId))}`;
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
        recoveryStoreFactory = () => new IndexedDbRecoveryStore(),
        recoveryFactory = (options) => new PrintRecoveryCoordinator(options),
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
        this.recoveryStoreFactory = recoveryStoreFactory;
        this.recoveryFactory = recoveryFactory;
        this.printerFactory = printerFactory;
        this.randomUUID = randomUUID;
        this.receiptPlans = new WeakMap();
        this.preparationPlans =
            preparationPlanBook || new PreparationPlanBook({ randomUUID: this.randomUUID });
        this.pos = null;
        this.binding = null;
        this.recovery = null;
        this.pairing = null;
        this.channels = new Map();
        this.printers = new Map();
        this.blockedPreparationOrders = new Set();
        this.planningPreparationOrders = new Set();
        this.activePreparationOrders = new Set();
        this.recoveryDialogOpen = false;
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
        if (!this.recovery) {
            this.recovery = this.recoveryFactory({
                store: this.recoveryStoreFactory({
                    databaseName: recoveryDatabaseName(this.bindings(), this.pos.session.id),
                }),
                clientForContext: (context, options) => this.clientForContext(context, options),
                onPreparationSettled: (result) => this.onPreparationSettled(result),
            });
            await this.recovery.restore();
        }
        for (const orderId of this.recovery.blockingPreparationOrderIds()) {
            this.blockedPreparationOrders.add(orderId);
        }
        await this.recovery.reconcile(undefined, { interactive: false });
        this.recovery.startWatching();
    }

    setCredentials(credentials) {
        this.providedCredentials = credentials;
        this.printers.clear();
        for (const channel of this.channels.values()) {
            channel.client = null;
        }
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
            channel = { manager, ready: false, restored: false, client: null };
            this.channels.set(key, channel);
        }
        return channel;
    }

    async credentialsFor(binding, { interactive = true } = {}) {
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
        if (!channel.ready && (!interactive || !(await this.ensurePairing(binding)))) {
            return null;
        }
        return channel.manager;
    }

    async clientFor(binding, options = {}) {
        if (!binding.agent_endpoint) {
            return null;
        }
        const credentials = await this.credentialsFor(binding, options);
        if (!credentials) {
            return null;
        }
        const channel = this.channelFor(binding);
        if (channel.client) {
            return channel.client;
        }
        channel.client = this.clientFactory({
            baseUrl: binding.agent_endpoint,
            credentials,
        });
        return channel.client;
    }

    bindings() {
        const bindings = [this.binding];
        for (const printer of this.pos?.unwatched?.printers || []) {
            bindings.push(printer.config.inari_preparation_binding);
        }
        return bindings.filter((binding) => binding?.authoritative === true);
    }

    async clientForContext(context, options = {}) {
        const { channel, ...clientOptions } = options;
        const bindings = this.bindings();
        if (channel) {
            const scopeMatches = bindings.some((binding) => [
                "browser_origin", "database", "company_id", "organization_id",
                "site_id", "pos_configuration_id",
            ].every((field) => String(binding[field]) === String(channel[field])));
            if (!scopeMatches) {
                throw new InariAgentError("recovery_scope_mismatch", "This recovery task belongs to another POS scope.");
            }
            return this.clientFor(channel, clientOptions);
        }
        const binding = bindings.find(
            (candidate) =>
                candidate.binding_revision_id === context.binding_revision_id &&
                candidate.device_id === context.device_id,
        );
        if (!binding?.agent_endpoint) {
            throw new InariAgentError(
                "agent_endpoint_required",
                "Configure the authenticated Agent Endpoint before printing.",
            );
        }
        return this.clientFor(binding, clientOptions);
    }

    async printerFor(binding) {
        const client = await this.clientFor(binding, { interactive: false });
        const key = printerKey(binding);
        const existing = this.printers.get(key);
        if (existing) {
            return existing;
        }
        const printer = this.printerFactory({
            client,
            recovery: this.recovery,
        });
        this.printers.set(key, printer);
        return printer;
    }

    isReceiptAuthoritative() {
        return Boolean(this.binding);
    }

    async prepareReceiptPrint(order) {
        if (!this.isReceiptAuthoritative()) {
            return null;
        }
        try {
            const copyOrdinal = Number(order?.nb_print || 0) + 1;
            const cached = this.receiptPlans.get(order);
            const previous = cached?.candidate;
            if (
                previous?.copy_ordinal === copyOrdinal &&
                previous.binding_revision_id === this.binding.binding_revision_id &&
                previous.device_id === this.binding.device_id &&
                previous.pos_session_id === String(this.pos.session.id)
            ) {
                return cached.promise;
            }
            const candidate = createReceiptPlan({
                binding: this.binding,
                order,
                posSessionId: this.pos.session.id,
                randomUUID: this.randomUUID,
            });
            const promise = (async () => {
                const saved = await this.recovery?.receiptContext(candidate);
                return saved ? Object.freeze({ ...candidate, print_intent_id: saved.print_intent_id }) : candidate;
            })();
            this.receiptPlans.set(order, { candidate, promise });
            return await promise;
        } catch (error) {
            return Object.freeze({ planningError: error });
        }
    }

    async printReceipt(element, plan) {
        if (plan?.planningError) {
            return this.fail("receipt_failed", plan.planningError);
        }
        const printer = await this.printerFor(this.binding);
        const result = await printer.printReceipt(element, plan, {
            order_reference:
                this.pos?.models?.["pos.order"]?.getBy("uuid", plan.offline_order_id)?.name ||
                plan.offline_order_id,
            printer_name: this.binding.device_name || this.binding.device_id,
            agent_channel: recoveryChannel(this.binding),
        });
        this.lastResult = result;
        if (!result.accepted) {
            const code =
                result.error instanceof InariAgentError ? result.error.code : "receipt_failed";
            const failure = this.fail(code, result.error, result);
            this.openRecovery();
            return failure;
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
        } else {
            const printer = await this.printerFor(binding);
            result = await printer.printReceipt(element, plan, {
                order_reference: source.order.name || source.order.uuid,
                printer_name: printerName,
                agent_channel: recoveryChannel(binding),
            });
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
        if (source?.order?.uuid) {
            this.blockPreparationAttempt(source.order.uuid, {
                tracked: Boolean(result.context?.print_intent_id),
            });
        }
        const message = {
            title: _t("Preparation ticket not sent"),
            body: _t(
                "Inari did not accept the ticket for %s. Check the printer and try again.",
                printerName,
            ),
        };
        if (!result.context?.print_intent_id) {
            this.notification?.add(message.body, {
                title: message.title,
                type: "danger",
                sticky: true,
            });
        }
        this.openRecovery();
        return {
            successful: false,
            canRetry: false,
            inari: true,
            state: result.state,
            printIntentId: result.context?.print_intent_id,
            message,
        };
    }

    recoverySnapshot() {
        return this.recovery?.snapshot() || [];
    }

    subscribeRecovery(listener) {
        return this.recovery?.subscribe(listener) || (() => {});
    }

    actOnRecovery(key, action) {
        return this.recovery.act(key, action);
    }

    openRecovery() {
        if (this.recoveryDialogOpen || !this.dialog || !this.recoverySnapshot().length) {
            return;
        }
        this.recoveryDialogOpen = true;
        this.dialog.add(InariRecoveryDialog, {
            recovery: {
                snapshot: () => this.recoverySnapshot(),
                subscribe: (listener) => this.subscribeRecovery(listener),
                act: (key, action) => this.actOnRecovery(key, action),
            },
            onClosed: () => {
                this.recoveryDialogOpen = false;
            },
        });
    }

    canAdvancePreparation(orderId) {
        return (
            !this.blockedPreparationOrders.has(orderId) &&
            !this.planningPreparationOrders.has(orderId) &&
            !this.recovery?.blocksPreparation(orderId)
        );
    }

    beginPreparationAttempt(orderId) {
        this.activePreparationOrders.add(orderId);
        this.planningPreparationOrders.delete(orderId);
    }

    blockPreparationAttempt(orderId, { tracked = false } = {}) {
        const blockers = tracked ? this.blockedPreparationOrders : this.planningPreparationOrders;
        blockers.add(orderId);
    }

    endPreparationAttempt(orderId) {
        this.activePreparationOrders.delete(orderId);
    }

    async onPreparationSettled({ offline_order_id: orderId }) {
        if (
            !this.blockedPreparationOrders.has(orderId) ||
            this.recovery?.blocksPreparation(orderId)
        ) {
            return;
        }
        this.blockedPreparationOrders.delete(orderId);
        if (this.activePreparationOrders.has(orderId)) {
            return;
        }
        const order = this.pos?.models?.["pos.order"]?.getBy("uuid", orderId);
        if (!order) {
            return;
        }
        order.updateLastOrderChange();
        if (!this.pos?.models?.["pos.prep.display"]?.length) {
            await this.pos.syncAllOrders({ orders: [order] });
        }
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
