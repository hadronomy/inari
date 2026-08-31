/** @odoo-module */

import { Component, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE_COPY = Object.freeze({
    submission_pending: "Waiting to send",
    pending_agent: "The Agent result needs checking",
    accepted: "Accepted by the Agent",
    in_progress: "Printing is in progress",
    failed: "The print did not finish",
    outcome_unknown: "Check the printer before another copy",
    expired: "The print request expired",
    canceled: "The print request was canceled",
    content_unavailable: "The exact print content is no longer in this tab",
});

export class InariRecoveryDialog extends Component {
    static template = "inari_devices.RecoveryDialog";
    static props = {
        close: Function,
        recovery: Object,
        onClosed: { type: Function, optional: true },
    };

    setup() {
        this.view = useState({
            entries: this.props.recovery.snapshot(),
            busyKey: null,
            confirmKey: null,
            error: null,
        });
        const unsubscribe = this.props.recovery.subscribe((entries) => {
            this.view.entries = entries;
            if (
                this.view.confirmKey &&
                !entries.some((entry) => entry.key === this.view.confirmKey)
            ) {
                this.view.confirmKey = null;
            }
        });
        onWillUnmount(() => {
            unsubscribe();
            this.props.onClosed?.();
        });
    }

    title(entry) {
        return entry.origin_kind === "preparation" ? "Kitchen ticket" : "Customer receipt";
    }

    stateCopy(entry) {
        return STATE_COPY[entry.state] || "The print needs attention";
    }

    retryLabel(entry) {
        return entry.origin_kind === "preparation" ? "Retry ticket" : "Retry receipt";
    }

    continueLabel(entry) {
        return entry.origin_kind === "preparation"
            ? "Continue without ticket"
            : "Continue without receipt";
    }

    confirm(entry) {
        this.view.confirmKey = entry.key;
        this.view.error = null;
    }

    keepWaiting() {
        this.view.confirmKey = null;
    }

    async act(entry, action) {
        this.view.busyKey = entry.key;
        this.view.error = null;
        try {
            await this.props.recovery.act(entry.key, action);
            this.view.confirmKey = null;
        } catch (error) {
            this.view.error =
                typeof error?.message === "string"
                    ? error.message
                    : "The recovery action did not finish.";
        } finally {
            this.view.busyKey = null;
        }
    }

    close() {
        this.props.close();
    }
}

export class InariRecoveryLauncher extends Component {
    static template = "inari_devices.RecoveryLauncher";

    setup() {
        this.inari = useService("inari_device");
        this.view = useState({ entries: this.inari.recoverySnapshot() });
        const unsubscribe = this.inari.subscribeRecovery((entries) => {
            this.view.entries = entries;
        });
        onWillUnmount(unsubscribe);
    }

    open() {
        this.inari.openRecovery();
    }
}

registry.category("main_components").add("InariRecoveryLauncher", {
    Component: InariRecoveryLauncher,
});
