/** @odoo-module */

import { HardwareProxy } from "@point_of_sale/app/services/hardware_proxy_service";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

patch(HardwareProxy.prototype, {
    async openCashbox(action = false) {
        const binding = this.pos?.config?.inari_cash_drawer_binding;
        if (binding?.authoritative !== true) {
            return super.openCashbox(...arguments);
        }
        const result = await this.inariDevice.openDrawer({ action });
        if (result.state === "succeeded" && action) {
            this.pos.logEmployeeMessage(action, "CASH_DRAWER_ACTION");
        }
        return result;
    },
});

patch(PosStore.prototype, {
    async openCashbox(action) {
        return this.hardwareProxy.openCashbox(action);
    },
});
