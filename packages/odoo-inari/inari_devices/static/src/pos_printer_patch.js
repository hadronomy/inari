/** @odoo-module */

import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import {
    PosPrinterService,
    posPrinterService,
} from "@point_of_sale/app/services/pos_printer_service";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

const INARI_RECEIPT_PLAN = Symbol("inari_receipt_plan");

if (!posPrinterService.dependencies.includes("inari_device")) {
    posPrinterService.dependencies.push("inari_device");
}
if (!PosStore.serviceDependencies.includes("inari_device")) {
    PosStore.serviceDependencies.push("inari_device");
}

patch(posPrinterService, {
    start(env, services) {
        return new PosPrinterService(env, services);
    },
});

patch(PosPrinterService.prototype, {
    setup(env, services) {
        super.setup(...arguments);
        this.inariDevice = services.inari_device;
    },

    async print(component, props, options = {}) {
        if (component !== OrderReceipt) {
            return super.print(...arguments);
        }
        const plan = this.inariDevice.prepareReceiptPrint(props?.order);
        if (!plan) {
            return super.print(...arguments);
        }
        return super.print(component, props, {
            ...options,
            [INARI_RECEIPT_PLAN]: plan,
        });
    },

    async printHtml(element, options = {}) {
        const plan = options[INARI_RECEIPT_PLAN];
        if (!plan) {
            return super.printHtml(...arguments);
        }
        const result = await this.inariDevice.printReceipt(element, plan);
        if (!result?.accepted) {
            return undefined;
        }
        return {
            successful: true,
            inari: true,
            printIntentId: result.context.print_intent_id,
            printJobId: result.print_job_id,
        };
    },
});

patch(PosStore.prototype, {
    async setup(env, services) {
        await super.setup(...arguments);
        await services.inari_device.attachPos(this);
    },
});
