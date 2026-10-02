/** @odoo-module */

import { BarcodeReader } from "@point_of_sale/app/services/barcode_reader_service";
import { patch } from "@web/core/utils/patch";

patch(BarcodeReader.prototype, {
    connectToProxy() {
        const binding = this.hardwareProxy.pos?.config?.inari_scanner_binding;
        if (binding?.authoritative === true) {
            this.remoteScanning = false;
            return;
        }
        return super.connectToProxy(...arguments);
    },
});
