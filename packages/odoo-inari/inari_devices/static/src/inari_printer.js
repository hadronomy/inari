/** @odoo-module */

import { htmlToCanvas } from "@point_of_sale/app/services/render_service";

import { ReceiptQueue } from "./receipt_queue";
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
    constructor({ client, contextStore, render = renderReceiptToJpeg, materialize } = {}) {
        if (!client || !contextStore || typeof render !== "function") {
            throw new TypeError(
                "InariReceiptPrinter requires a client, context store, and renderer",
            );
        }
        this.render = render;
        this.materialize = materialize || materializeSubmissionContext;
        this.queue = new ReceiptQueue({
            contextStore,
            submit: (context, jpeg) => client.submit(context, jpeg),
        });
    }

    async printReceipt(element, plan) {
        try {
            const jpeg = await this.render(element);
            const context = await this.materialize(plan, jpeg);
            const entry = await this.queue.enqueue(context, jpeg);
            if (entry.state !== "accepted") {
                return {
                    accepted: false,
                    state: entry.state,
                    error: entry.error,
                    context,
                };
            }
            return {
                accepted: true,
                context,
                ...entry.result,
            };
        } catch (error) {
            return {
                accepted: false,
                state: "failed",
                error,
            };
        }
    }

    retry(key) {
        return this.queue.retry(key);
    }

    snapshot() {
        return this.queue.snapshot();
    }
}
