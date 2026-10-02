/** @odoo-module */

import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { RetryPrintPopup } from "@point_of_sale/app/components/popups/retry_print_popup/retry_print_popup";
import { changesToOrder } from "@point_of_sale/app/models/utils/order_change";
import {
    PosPrinterService,
    posPrinterService,
} from "@point_of_sale/app/services/pos_printer_service";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { renderToElement } from "@web/core/utils/render";

import {
    markPreparationSegments,
    markPreparationSource,
    preparationSource,
} from "./preparation_print";

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
        const plan = await this.inariDevice.prepareReceiptPrint(props?.order);
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

    generateOrderChange(order, orderChange, categories, reprint = false) {
        const result = super.generateOrderChange(...arguments);
        if (
            this.unwatched?.printers?.some(
                (printer) => printer.config.inari_preparation_binding?.authoritative === true,
            )
        ) {
            markPreparationSource(result.orderData, { order, orderChange, reprint });
        }
        return result;
    },

    async generateReceiptsDataToPrint(orderData, changes, orderChange) {
        const receiptsData = await super.generateReceiptsDataToPrint(...arguments);
        return markPreparationSegments(receiptsData, orderData, changes, orderChange);
    },

    async sendOrderInPreparation(order, opts = {}) {
        if (!hasAuthoritativePreparation(this.unwatched?.printers)) {
            return super.sendOrderInPreparation(...arguments);
        }
        let isPrinted = false;
        let canAdvance = true;
        const inariDevice = this.env.services.inari_device;
        inariDevice.beginPreparationAttempt(order.uuid);
        try {
            this.syncingOrders.add(order.uuid);
            if (this.config.printerCategories.size && !opts.byPassPrint) {
                const orderChange = changesToOrder(
                    order,
                    this.config.printerCategories,
                    opts.cancelled,
                );
                const hasChanges =
                    orderChange.new.length ||
                    orderChange.cancelled.length ||
                    orderChange.noteUpdate.length ||
                    orderChange.internal_note ||
                    orderChange.general_customer_note;
                if (!order.uiState.isReprinting) {
                    order.uiState.lastPrints.push(orderChange);
                }
                if (hasChanges) {
                    isPrinted = await this.printChanges(order, [orderChange]);
                }
            }
            canAdvance = inariDevice.canAdvancePreparation(order.uuid);
            if (canAdvance) {
                order.updateLastOrderChange();
            }
        } finally {
            this.syncingOrders.delete(order.uuid);
            inariDevice.endPreparationAttempt(order.uuid);
        }
        if (isPrinted && canAdvance && !this.models["pos.prep.display"]?.length) {
            await this.syncAllOrders({ orders: [order] });
        }
    },

    async printChanges(order, orderChange, reprint = false, printers = this.unwatched.printers) {
        if (!hasAuthoritativePreparation(printers)) {
            return super.printChanges(...arguments);
        }
        let isPrinted = false;
        const nativeFailures = [];
        const nativeRetryPrinters = new Set();

        for (const printer of printers) {
            for (const change of orderChange) {
                const { orderData, changes } = this.generateOrderChange(
                    order,
                    change,
                    printer.config.product_categories_ids,
                    reprint,
                );
                const receiptsData = await this.generateReceiptsDataToPrint(
                    orderData,
                    changes,
                    change,
                );
                for (const data of receiptsData) {
                    // oxlint-disable-next-line no-await-in-loop
                    const result = await this.printOrderChanges(data, printer);
                    if (result.successful) {
                        isPrinted = true;
                        if (result.warningCode) {
                            this.displayPrinterWarning(result, printer.config.name);
                        }
                    } else if (result.inari) {
                        this.env.services.inari_device.blockPreparationAttempt(order.uuid, {
                            tracked: Boolean(result.printIntentId),
                        });
                    } else if (!result.inari) {
                        nativeRetryPrinters.add(printer);
                        nativeFailures.push(
                            `${printer.config.name}: ${result.message?.body || "Print failed"}`,
                        );
                    }
                }
            }
        }

        if (nativeFailures.length) {
            this.dialog.add(RetryPrintPopup, {
                message: nativeFailures.join("\n"),
                canRetry: true,
                retry: () => {
                    this.printChanges(order, orderChange, reprint, nativeRetryPrinters);
                },
            });
        }
        return isPrinted;
    },

    async printOrderChanges(data, printer) {
        const binding = printer?.config?.inari_preparation_binding;
        if (binding?.authoritative !== true) {
            return super.printOrderChanges(...arguments);
        }
        const source = preparationSource(data);
        const plan = await servicesPlan(this, binding, source);
        const receipt = renderToElement("point_of_sale.OrderChangeReceipt", { data });
        return this.env.services.inari_device.printPreparation(
            receipt,
            plan,
            binding,
            source,
            printer.config.name,
        );
    },
});

function hasAuthoritativePreparation(printers) {
    return Boolean(
        [...(printers || [])].some(
            (printer) => printer.config.inari_preparation_binding?.authoritative === true,
        ),
    );
}

async function servicesPlan(store, binding, source) {
    if (!source) {
        return Object.freeze({
            planningError: new TypeError("The preparation receipt source is missing"),
        });
    }
    return store.env.services.inari_device.preparePreparationPrint(binding, source);
}
