/** @odoo-module */

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { InariAgentClient } from "./agent_client";
import { ClientPairingManager } from "./client_pairing";
import { IndexedDbRecoveryStore } from "./recovery_store";
import { TEST_RECEIPTS, TestReceiptRunner } from "./test_receipt_runner";

const STATES = {
    submission_pending: _t("Preparing"),
    pending_agent: _t("Check result before printing again"),
    accepted: _t("Queued"),
    in_progress: _t("Printing"),
    output_confirmed: _t("Output confirmed"),
    failed: _t("Failed"),
    outcome_unknown: _t("Outcome unknown — check the printer"),
    expired: _t("Expired"),
    canceled: _t("Canceled"),
    content_unavailable: _t("Check result"),
};

function boundedFetch(url, options = {}) {
    return fetch(url, { ...options, signal: options.signal || AbortSignal.timeout(15000) });
}

export class TestReceiptDialog extends Component {
    static template = "inari_devices.TestReceiptDialog";
    static components = { Dialog };
    static props = { close: Function, deviceId: { type: [Number, Boolean], optional: true } };

    setup() {
        this.orm = useService("orm");
        this.receipts = TEST_RECEIPTS.map((receipt) => ({
            ...receipt,
            name: receipt.id === "inari" ? _t("INARI check") : _t("MIZONA sample"),
            description:
                receipt.id === "inari"
                    ? _t("Text, Spanish accents, Code 128 barcode, QR code, and feed/cut.")
                    : _t("Logo, products, tax, discount, payments, loyalty, barcode, and QR code."),
        }));
        this.title = _t("Test receipts");
        this.state = useState({
            printers: [],
            printerId: "",
            channelIndex: "0",
            selection: "both",
            busy: false,
            rows: [],
            error: "",
            pairing: null,
            physicallyChecked: false,
        });
        this.clients = new Map();
        this.managers = new Map();
        this.alive = true;
        this.timer = null;
        this.runner = new TestReceiptRunner({
            store: new IndexedDbRecoveryStore({ databaseName: "inari-test-receipt-recovery" }),
            clientForContext: (_context, { channel }) => this.clientForChannel(channel),
            fetchApi: boundedFetch,
        });
        onWillStart(async () => {
            this.state.printers = await this.orm.call("inari.device", "get_test_receipt_options", [
                this.props.deviceId || false,
            ]);
            this.state.printerId = String(this.props.deviceId || this.state.printers[0]?.id || "");
            await this.restore();
        });
        onWillUnmount(() => {
            this.alive = false;
            clearTimeout(this.timer);
            for (const manager of this.managers.values()) {
                if (manager.snapshot().name !== "ready") void manager.cancel();
            }
        });
    }

    get printer() {
        return this.state.printers.find(({ id }) => String(id) === this.state.printerId);
    }
    get channel() {
        return this.printer?.channels[Number(this.state.channelIndex)];
    }
    get canPrint() {
        return Boolean(this.channel) && !this.state.busy && !this.state.rows.length;
    }
    get canClear() {
        return (
            !this.state.busy &&
            this.runner.canClear &&
            (!this.runner.needsPhysicalCheck || this.state.physicallyChecked)
        );
    }
    get needsPhysicalCheck() {
        return this.runner.needsPhysicalCheck;
    }
    get printLabel() {
        return this.state.selection === "both"
            ? _t("Print both receipts")
            : _t("Print selected receipt");
    }
    stateLabel(value) {
        return STATES[value] || value;
    }
    receiptLabel(value) {
        return value === "INARI check"
            ? _t("INARI check")
            : value === "MIZONA sample"
              ? _t("MIZONA sample")
              : value;
    }

    async selectPrinter(event) {
        if (this.state.busy) return;
        this.state.busy = true;
        this.state.printerId = event.target.value;
        this.state.channelIndex = "0";
        this.state.error = "";
        this.state.physicallyChecked = false;
        try {
            await this.restore();
        } catch (error) {
            this.state.error = error.message;
        } finally {
            this.state.busy = false;
        }
    }

    async restore() {
        if (!this.printer) return;
        this.state.rows = await this.runner.restore(this.printer.device_id);
        this.scheduleRefresh();
    }

    async clientForChannel(channel) {
        if (!channel) throw new Error(_t("The saved printer authorization is unavailable."));
        const key = JSON.stringify([channel.binding, channel.pos_session_id]);
        if (this.clients.has(key)) return this.clients.get(key);
        let manager = this.managers.get(key);
        if (!manager) {
            manager = new ClientPairingManager({
                binding: channel.binding,
                posSessionId: channel.pos_session_id,
                rpc,
                fetchApi: boundedFetch,
            });
            this.managers.set(key, manager);
            manager.subscribe((pairing) => {
                if (this.alive) this.state.pairing = pairing;
            });
        }
        if (!(await manager.restore())) await manager.begin();
        if (!this.alive) throw new Error(_t("The test receipt dialog was closed."));
        this.state.pairing = null;
        const client = new InariAgentClient({
            baseUrl: channel.binding.agent_endpoint,
            credentials: manager,
            fetchApi: boundedFetch,
        });
        this.clients.set(key, client);
        return client;
    }

    async print() {
        if (!this.canPrint) return;
        this.state.busy = true;
        this.state.error = "";
        const printer = this.printer;
        const channel = this.channel;
        const selection = this.state.selection;
        try {
            const client = await this.clientForChannel(channel);
            this.state.rows = await this.runner.print({ printer, channel, selection, client });
        } catch (error) {
            this.state.rows = this.runner.rows;
            this.state.error = error.message;
        } finally {
            this.state.busy = false;
            this.scheduleRefresh();
        }
    }

    scheduleRefresh() {
        clearTimeout(this.timer);
        if (
            this.alive &&
            this.state.rows.some(({ state }) => ["accepted", "in_progress"].includes(state))
        ) {
            this.timer = setTimeout(() => this.refresh(), 2000);
        }
    }

    async refresh() {
        if (this.state.busy) return;
        this.state.busy = true;
        this.state.error = "";
        try {
            this.state.rows = await this.runner.refresh();
        } catch (error) {
            this.state.error = error.message;
        } finally {
            this.state.busy = false;
            this.scheduleRefresh();
        }
    }

    async newTest() {
        if (!this.canClear) return;
        this.state.busy = true;
        try {
            await this.runner.clear({ physicallyChecked: this.state.physicallyChecked });
            this.state.rows = [];
            this.state.physicallyChecked = false;
            this.state.error = "";
        } catch (error) {
            this.state.error = error.message;
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("inari_devices.test_receipts", (env, action) => {
    env.services.dialog.add(TestReceiptDialog, { deviceId: action.params?.device_id || false });
});
