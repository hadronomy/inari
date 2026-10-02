/** @odoo-module */

import {
    PosScaleService,
    posScaleService,
} from "@point_of_sale/app/screens/scale_screen/scale_service";
import { patch } from "@web/core/utils/patch";

const UNIT_ALIASES = Object.freeze({
    g: new Set(["g", "gram", "grams"]),
    kg: new Set(["kg", "kilogram", "kilograms"]),
    lb: new Set(["lb", "lbs", "pound", "pounds"]),
    oz: new Set(["oz", "ounce", "ounces"]),
});

function normalizedUnit(value) {
    return String(value || "")
        .trim()
        .toLowerCase();
}

if (!posScaleService.dependencies.includes("inari_device")) {
    posScaleService.dependencies.push("inari_device");
}

patch(PosScaleService.prototype, {
    setup(env, dependencies) {
        super.setup(...arguments);
        this.inariDevice = dependencies.inari_device;
        this.inariScaleValid = false;
        this.inariScaleReason = "inactive";
        this.inariDevice.registerScaleService(this);
    },

    start(errorCallback) {
        if (!this.inariDevice?.isScaleAuthoritative()) return super.start(...arguments);
        this.onError = errorCallback;
        this.isMeasuring = true;
        this.loading = true;
        void this.inariDevice
            .activateScale()
            .then((active) => {
                if (!active) this.invalidateInariReading("unavailable");
            })
            .catch((error) => {
                this.invalidateInariReading("unavailable");
                this.onError?.(error.message);
            });
    },

    reset() {
        const authoritative = this.inariDevice?.isScaleAuthoritative() === true;
        if (authoritative) void this.inariDevice.deactivateScale();
        super.reset(...arguments);
        this.inariScaleValid = false;
        this.inariScaleReason = "inactive";
    },

    async readWeight() {
        if (this.inariDevice?.isScaleAuthoritative()) return;
        return super.readWeight(...arguments);
    },

    confirmWeight() {
        const weight = super.confirmWeight(...arguments);
        if (this.inariDevice?.isScaleAuthoritative()) {
            this.inariDevice.consumeScaleReading();
            this.inariScaleValid = false;
            this.inariScaleReason = "consumed";
        }
        return weight;
    },

    get isWeightValid() {
        if (!this.inariDevice?.isScaleAuthoritative()) return super.isWeightValid;
        return this.inariScaleValid && this.netWeight > 0 && super.isWeightValid;
    },

    isInariUnitCompatible(unit) {
        const expected = normalizedUnit(this.product?.unitOfMeasure);
        const aliases = UNIT_ALIASES[unit];
        return aliases ? aliases.has(expected) : normalizedUnit(unit) === expected;
    },

    acceptInariReading(reading) {
        const weight = Number(reading.decimal);
        if (!Number.isFinite(weight) || weight <= 0) {
            this.invalidateInariReading("conversion_invalid");
            return;
        }
        this.weight = weight;
        this.loading = false;
        this.inariScaleValid = true;
        this.inariScaleReason = null;
        // These native helpers preserve the Odoo last-weight and tare lifecycle.
        // oxlint-disable-next-line no-underscore-dangle
        this._clearLastWeightIfValid();
        // oxlint-disable-next-line no-underscore-dangle
        this._setTareIfRequested();
    },

    invalidateInariReading(reason) {
        this.inariScaleValid = false;
        this.inariScaleReason = reason;
        this.loading = [
            "candidate",
            "connecting",
            "reconnecting",
            "reconciling",
            "stabilizing",
        ].includes(reason);
    },
});
