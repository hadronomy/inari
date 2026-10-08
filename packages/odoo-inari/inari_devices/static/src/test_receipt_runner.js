/** @odoo-module */

import { PrintRecoveryCoordinator } from "./print_recovery";
import { createLocalPrintPlan, materializeSubmissionContext } from "./submission_context";

const CLEARABLE_STATES = new Set([
    "output_confirmed",
    "failed",
    "expired",
    "canceled",
    "outcome_unknown",
    "content_unavailable",
]);

export const TEST_RECEIPTS = Object.freeze([
    Object.freeze({
        id: "inari",
        name: "INARI check",
        description: "Text, Spanish accents, Code 128 barcode, QR code, and feed/cut.",
        url: "/inari_devices/static/img/test_receipts/inari.jpg",
    }),
    Object.freeze({
        id: "mizona",
        name: "MIZONA sample",
        description: "Logo, products, tax, discount, payments, loyalty, barcode, and QR code.",
        url: "/inari_devices/static/img/test_receipts/mizona.jpg",
    }),
]);

/** Reconcile a saved test batch before any new receipt can enter the queue. */
export class TestReceiptRunner {
    constructor({ store, clientForContext, fetchApi = fetch, cryptoApi = crypto }) {
        this.store = store;
        this.fetchApi = fetchApi;
        this.cryptoApi = cryptoApi;
        this.rows = [];
        this.busy = false;
        this.recovery = new PrintRecoveryCoordinator({ store, clientForContext });
    }

    async restore(deviceId) {
        await this.recovery.restore();
        this.rows = (await this.store.list())
            .filter((record) => record.context.device_id === deviceId)
            .map((record) => ({
                key: record.key,
                name: record.descriptor.order_reference,
                state: record.state,
                error: record.failure_code,
            }));
        return this.rows;
    }

    async print({ printer, channel, selection, client }) {
        if (this.busy || this.rows.length) return this.rows;
        const receipts = TEST_RECEIPTS.filter(({ id }) => selection === "both" || selection === id);
        if (!receipts.length || channel.binding.device_id !== printer.device_id) {
            throw new TypeError("Select a receipt and the exact authorized printer");
        }
        this.busy = true;
        try {
            const batch = [];
            for (const receipt of receipts) {
                // oxlint-disable-next-line no-await-in-loop
                const response = await this.fetchApi(receipt.url, { credentials: "same-origin" });
                if (!response.ok)
                    throw new Error(`Cannot load ${receipt.name}. No receipt was sent.`);
                // oxlint-disable-next-line no-await-in-loop
                const jpeg = await response.blob();
                if (jpeg.type !== "image/jpeg")
                    throw new TypeError("The test receipt must be JPEG");
                const preparation = channel.binding.purpose === "pos_preparation";
                // oxlint-disable-next-line no-await-in-loop
                const digest = preparation
                    ? new Uint8Array(
                          await this.cryptoApi.subtle.digest("SHA-256", await jpeg.arrayBuffer()),
                      )
                    : null;
                const plan = createLocalPrintPlan({
                    binding: channel.binding,
                    order: { uuid: this.cryptoApi.randomUUID(), nb_print: 0 },
                    posSessionId: channel.pos_session_id,
                    copyOrdinal: 1,
                    documentKind: preparation ? "preparation_ticket" : "customer_receipt",
                    originKind: preparation ? "preparation" : "pos",
                    preparationRevision: digest
                        ? `sha256:${[...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`
                        : null,
                    segmentIndex: preparation ? 0 : null,
                    segmentKind: preparation ? "new" : null,
                    randomUUID: () => this.cryptoApi.randomUUID(),
                });
                // oxlint-disable-next-line no-await-in-loop
                const context = await materializeSubmissionContext(plan, jpeg);
                batch.push({ receipt, jpeg, context });
            }
            this.rows = batch.map(({ receipt, context }) => ({
                key: context.print_intent_id,
                name: receipt.name,
                state: "submission_pending",
                error: null,
            }));
            for (const [index, { receipt, jpeg, context }] of batch.entries()) {
                // oxlint-disable-next-line no-await-in-loop
                const result = await this.recovery.enqueue({
                    context,
                    jpeg,
                    client,
                    descriptor: {
                        order_reference: receipt.name,
                        printer_name: printer.name,
                        agent_channel: {
                            binding: channel.binding,
                            pos_session_id: channel.pos_session_id,
                        },
                    },
                });
                this.rows[index].state = result.state;
                this.rows[index].error = result.error?.message || null;
            }
            return this.rows;
        } finally {
            this.busy = false;
        }
    }

    async refresh() {
        if (this.busy || !this.rows.length) return this.rows;
        this.busy = true;
        try {
            await this.recovery.reconcile(this.rows.map(({ key }) => key));
            for (const row of this.rows) {
                // oxlint-disable-next-line no-await-in-loop
                const result = await this.recovery.knownResult(row.key);
                if (result) {
                    row.state = result.state;
                    row.error = result.error?.message || null;
                }
            }
            return this.rows;
        } finally {
            this.busy = false;
        }
    }

    get canClear() {
        return (
            !this.busy &&
            this.rows.length > 0 &&
            this.rows.every(({ state }) => CLEARABLE_STATES.has(state))
        );
    }

    get needsPhysicalCheck() {
        return this.rows.some(({ state }) =>
            ["outcome_unknown", "content_unavailable"].includes(state),
        );
    }

    async clear({ physicallyChecked = false } = {}) {
        if (!this.canClear || (this.needsPhysicalCheck && !physicallyChecked)) {
            throw new Error(
                "Check the printer and reconcile pending jobs before starting another test",
            );
        }
        this.busy = true;
        try {
            for (const { key, state } of this.rows) {
                if (state !== "output_confirmed") {
                    // oxlint-disable-next-line no-await-in-loop
                    await this.recovery.act(key, "finish_without_ticket");
                }
            }
            this.rows = [];
        } finally {
            this.busy = false;
        }
    }
}
