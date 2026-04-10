/** @odoo-module */

const DEFAULT_CONFIG = {
    enabled: false,
    agent_url: "http://127.0.0.1:7310",
    receipt_mode: "payload",
    auto_print: true,
    allow_manual_reprint: true,
    open_cashdrawer: false,
    timeout_ms: 4500,
    debug: false,
    default_printer_name: "",
    kitchen_printer_name: "",
    scale_enabled: false,
    customer_display_enabled: false,
    scanner_enabled: true,
};

class PosIotBridgeService {
    constructor() {
        this.config = { ...DEFAULT_CONFIG };
        this.status = {
            healthy: false,
            lastError: null,
            printer: null,
            drawer_supported: false,
            fetched: false,
        };
    }

    debug(...args) {
        if (this.config.debug) {
            console.debug("[pos_iot_bridge]", ...args);
        }
    }

    async fetchConfig() {
        try {
            const configId = window.odoo?.pos_config_id;
            const response = await this._jsonRpc("/pos_iot_bridge/config", { config_id: configId });
            if (response?.ok) {
                this.config = { ...DEFAULT_CONFIG, ...(response.config || {}) };
            }
            this.status.fetched = true;
            return this.config;
        } catch (error) {
            this.status.lastError = this._normalizeError(error, "CONFIG_FETCH_FAILED");
            return this.config;
        }
    }

    isEnabled() {
        return Boolean(this.config.enabled);
    }

    async healthCheck() {
        if (!this.isEnabled()) {
            return { ok: true, disabled: true };
        }
        try {
            const response = await this._fetchAgent("/health", { method: "GET" });
            this.status.healthy = Boolean(response.ok);
            this.status.lastError = null;
            this.status.printer = response.default_printer || response.printer || null;
            this.status.drawer_supported = Boolean(response.drawer_supported);
            return response;
        } catch (error) {
            this.status.healthy = false;
            this.status.lastError = this._normalizeError(error, "AGENT_UNREACHABLE");
            throw this.status.lastError;
        }
    }

    async printOrder(order, opts = {}) {
        await this.ensureReady();
        const receipt = order.export_for_printing();
        if (this.config.receipt_mode === "html") {
            const html = document.querySelector(".pos-receipt")?.outerHTML || "";
            if (!html) {
                throw this._normalizeError(new Error("Receipt HTML not found"), "RECEIPT_HTML_NOT_FOUND");
            }
            return this.printHtml(html, { order_name: order.name, ...opts });
        }
        return this.printReceiptPayload(receipt, { order_name: order.name, ...opts });
    }

    async printReceiptPayload(receipt, opts = {}) {
        return this._fetchAgent("/print_receipt", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source: "odoo_export_for_printing",
                receipt,
                printer_name: opts.printer_name || this.config.default_printer_name || null,
                open_drawer: opts.open_drawer ?? this.config.open_cashdrawer,
                metadata: { order_name: opts.order_name || receipt.name || null },
            }),
        });
    }

    async printHtml(html, opts = {}) {
        return this._fetchAgent("/print_html", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                html,
                printer_name: opts.printer_name || this.config.default_printer_name || null,
                open_drawer: opts.open_drawer ?? this.config.open_cashdrawer,
                metadata: { order_name: opts.order_name || null },
            }),
        });
    }

    async openDrawer(printerName = null) {
        await this.ensureReady();
        return this._fetchAgent("/open_drawer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ printer_name: printerName || this.config.default_printer_name || null }),
        });
    }

    async listPrinters() {
        await this.ensureReady();
        return this._fetchAgent("/printers", { method: "GET" });
    }

    async testPrint() {
        await this.ensureReady();
        return this._fetchAgent("/test_print", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ printer_name: this.config.default_printer_name || null }),
        });
    }

    async ensureReady() {
        if (!this.status.fetched) {
            await this.fetchConfig();
        }
        if (!this.isEnabled()) {
            throw {
                code: "IOT_BRIDGE_DISABLED",
                message: "POS IoT Bridge is disabled for this POS config.",
            };
        }
    }

    async _jsonRpc(route, params) {
        const response = await fetch(route, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params, id: Date.now() }),
        });
        const payload = await response.json();
        if (payload.error) {
            throw new Error(payload.error.data?.message || payload.error.message || "JSON-RPC request failed");
        }
        return payload.result;
    }

    async _fetchAgent(path, options = {}) {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), this.config.timeout_ms || 4500);
        try {
            const response = await fetch(`${this.config.agent_url}${path}`, {
                ...options,
                signal: controller.signal,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok === false) {
                const error = new Error(payload.message || `IoT bridge call failed with ${response.status}`);
                throw this._normalizeError(error, payload.code || `HTTP_${response.status}`);
            }
            return payload;
        } finally {
            window.clearTimeout(timeout);
        }
    }

    _normalizeError(error, code = "IOT_BRIDGE_ERROR") {
        return {
            code,
            message: error?.message || "Unexpected IoT bridge error.",
            raw: error,
        };
    }
}

export const posIotBridge = new PosIotBridgeService();
