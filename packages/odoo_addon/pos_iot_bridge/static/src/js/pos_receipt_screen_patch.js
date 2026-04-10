/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";
import { onMounted } from "@odoo/owl";
import { posIotBridge } from "./pos_iot_bridge_service";

patch(ReceiptScreen.prototype, {
    setup() {
        super.setup(...arguments);
        onMounted(async () => {
            await posIotBridge.fetchConfig();
            if (!posIotBridge.isEnabled() || !posIotBridge.config.auto_print) {
                return;
            }
            try {
                await posIotBridge.printOrder(this.currentOrder, {
                    open_drawer: posIotBridge.config.open_cashdrawer,
                });
                this.notification.add("Receipt sent to local IoT bridge.", { type: "success" });
            } catch (error) {
                this.notification.add(`Local print failed: ${error.message || error.code}`, { type: "danger" });
                posIotBridge.debug("Auto print failed", error);
            }
        });
    },

    async iotBridgeManualPrint() {
        try {
            await posIotBridge.fetchConfig();
            await posIotBridge.printOrder(this.currentOrder, {
                open_drawer: false,
            });
            this.notification.add("Receipt reprinted through local IoT bridge.", { type: "success" });
        } catch (error) {
            this.notification.add(`Reprint failed: ${error.message || error.code}`, { type: "danger" });
        }
    },

    async iotBridgeOpenDrawer() {
        try {
            await posIotBridge.fetchConfig();
            await posIotBridge.openDrawer();
            this.notification.add("Cash drawer command sent.", { type: "success" });
        } catch (error) {
            this.notification.add(`Drawer command failed: ${error.message || error.code}`, { type: "danger" });
        }
    },
});
