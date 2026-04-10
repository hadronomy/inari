/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";

patch(Order.prototype, {
    export_for_printing() {
        const result = super.export_for_printing(...arguments);
        result.iot_bridge = {
            exported_at: new Date().toISOString(),
            order_uuid: this.uuid,
            order_name: this.name,
        };
        return result;
    },
});
