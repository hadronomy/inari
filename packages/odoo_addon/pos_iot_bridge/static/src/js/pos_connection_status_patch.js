/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";
import { onMounted } from "@odoo/owl";
import { posIotBridge } from "./pos_iot_bridge_service";

patch(ReceiptScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.iotBridgeStatus = posIotBridge.status;
        onMounted(async () => {
            try {
                await posIotBridge.fetchConfig();
                await posIotBridge.healthCheck();
            } catch {
                // status is updated inside the service
            }
        });
    },
});
