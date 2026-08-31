/** @odoo-module */

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";

export class ClientPairingDialog extends Component {
    static template = "inari_devices.ClientPairingDialog";
    static props = {
        close: Function,
        manager: Object,
    };

    setup() {
        this.view = useState({ current: this.props.manager.snapshot() });
        const unsubscribe = this.props.manager.subscribe((current) => {
            this.view.current = current;
        });
        onWillStart(async () => {
            try {
                await this.props.manager.begin();
            } catch {
                // The observable state contains the safe operator message.
            }
        });
        onWillUnmount(unsubscribe);
    }

    get state() {
        return this.view.current;
    }

    get expiresAt() {
        if (!this.state.expiresAt) {
            return "";
        }
        return new Intl.DateTimeFormat(undefined, {
            hour: "2-digit",
            minute: "2-digit",
        }).format(new Date(this.state.expiresAt));
    }

    async retry() {
        try {
            await this.props.manager.begin();
        } catch {
            // The dialog stays open with the new failure state.
        }
    }

    async cancel() {
        await this.props.manager.cancel();
        this.props.close();
    }
}
