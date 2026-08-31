/** @odoo-module */

import { htmlToCanvas } from "@point_of_sale/app/services/render_service";

import { materializeSubmissionContext } from "./submission_context";

function canvasToJpeg(canvas, quality) {
    return new Promise((resolve, reject) => {
        canvas.toBlob(
            (blob) => {
                if (blob) {
                    resolve(blob);
                } else {
                    reject(new Error("The receipt JPEG encoder returned no document."));
                }
            },
            "image/jpeg",
            quality,
        );
    });
}

/** Use Odoo's receipt renderer so Inari output matches native IoT output. */
export async function renderReceiptToJpeg(element, { quality = 0.92 } = {}) {
    const canvas = await htmlToCanvas(element, { addClass: "pos-receipt-print" });
    return canvasToJpeg(canvas, quality);
}

/** Render, identify, queue, and admit one physical customer-receipt copy. */
export class InariReceiptPrinter {
    constructor({ client, recovery, render = renderReceiptToJpeg, materialize } = {}) {
        if (!recovery || typeof render !== "function") {
            throw new TypeError("InariReceiptPrinter requires a recovery coordinator and renderer");
        }
        this.client = client;
        this.recovery = recovery;
        this.render = render;
        this.materialize = materialize || materializeSubmissionContext;
    }

    async printReceipt(element, plan, descriptor = {}) {
        try {
            const existing = this.recovery.knownResult(plan?.print_intent_id);
            if (existing) {
                return existing;
            }
            const jpeg = await this.render(element);
            const context = await this.materialize(plan, jpeg);
            return this.recovery.enqueue({
                context,
                jpeg,
                client: this.client,
                descriptor,
            });
        } catch (error) {
            return {
                accepted: false,
                state: "failed",
                error,
            };
        }
    }
}
