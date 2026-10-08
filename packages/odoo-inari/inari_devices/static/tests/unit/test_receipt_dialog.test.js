import { expect, test } from "@odoo/hoot";
import {
    clearRegistry,
    contains,
    makeDialogMockEnv,
    mockService,
    mountWithCleanup,
} from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { TestReceiptDialog } from "../../src/test_receipt_dialog";

class ReceiptDialog extends TestReceiptDialog {
    async restore() {
        this.state.rows = [];
    }
}

test("test receipts render printer selection and browser approval steps", async () => {
    const services = registry.category("services");
    clearRegistry(registry.category("main_components"));
    const dialogServices = ["ui", "hotkey", "localization"].map((name) => [
        name,
        services.get(name),
    ]);
    clearRegistry(services);
    for (const [name, service] of dialogServices) services.add(name, service);
    const printers = [42, 73].map((id) => ({
        id,
        name: `Printer ${id}`,
        company: "Test company",
        device_id: `device-${id}`,
        channels: [{ label: `POS ${id}` }],
    }));
    mockService("orm", () => ({
        async call(model, method, args) {
            expect(model).toBe("inari.device");
            expect(method).toBe("get_test_receipt_options");
            expect(args).toEqual([73]);
            return printers;
        },
    }));
    await makeDialogMockEnv();
    const dialog = await mountWithCleanup(ReceiptDialog, {
        props: { close() {}, deviceId: 73 },
    });

    expect(".o_dialog").toHaveCount(1);
    expect("#inari-test-printer").toHaveValue("73");
    expect(dialog.printer.id).toBe(73);
    expect(dialog.channel.label).toBe("POS 73");

    dialog.state.pairing = {
        name: "awaiting_approval",
        requestId: "req_receipt_test",
        phrase: "maple river cloud stone",
    };
    await contains("#inari-test-printer").select("42");

    expect("#inari-test-printer").toHaveValue("42");
    expect(dialog.printer.id).toBe(42);
    expect(dialog.channel.label).toBe("POS 42");
    expect("#inari-test-pairing-request").toHaveValue("req_receipt_test");
    expect(".inari-test-receipts a").toHaveAttribute("href", "inari://pairing/req_receipt_test");
    expect(".inari-test-receipts").toHaveText(/Review request/);
    expect(".inari-test-receipts").toHaveText(/Approve browser/);
    expect(".inari-test-receipts").toHaveText(/maple river cloud stone/);
});
