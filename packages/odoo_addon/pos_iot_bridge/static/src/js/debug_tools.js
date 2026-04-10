/** @odoo-module */

import { posIotBridge } from "./pos_iot_bridge_service";

window.posIotBridge = {
    service: posIotBridge,
    async testPrint() {
        await posIotBridge.fetchConfig();
        return posIotBridge.testPrint();
    },
    async health() {
        await posIotBridge.fetchConfig();
        return posIotBridge.healthCheck();
    },
    async printers() {
        await posIotBridge.fetchConfig();
        return posIotBridge.listPrinters();
    },
};
