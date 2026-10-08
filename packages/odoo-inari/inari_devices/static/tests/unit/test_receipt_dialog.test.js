import { expect, test } from "@odoo/hoot";
import {
    contains,
    makeDialogMockEnv,
    mockService,
    mountWithCleanup,
} from "@web/../tests/web_test_helpers";
import { TestReceiptDialog } from "../../src/test_receipt_dialog";

class ReceiptDialog extends TestReceiptDialog {
    async restore() {
        this.state.rows = [];
    }
}

test("test receipts render and select numeric printer IDs", async () => {
    const printers = [42, 73].map((id) => ({
        id,
        name: `Printer ${id}`,
        company: "Test company",
        device_id: `device-${id}`,
        channels: [{ label: `POS ${id}` }],
    }));
    mockService("orm", {
        async call(model, method, args) {
            expect(model).toBe("inari.device");
            expect(method).toBe("get_test_receipt_options");
            expect(args).toEqual([73]);
            return printers;
        },
    });
    await makeDialogMockEnv();
    const dialog = await mountWithCleanup(ReceiptDialog, {
        props: { close() {}, deviceId: 73 },
    });

    expect(".o_dialog").toHaveCount(1);
    expect("#inari-test-printer").toHaveValue("73");
    expect(dialog.printer.id).toBe(73);
    expect(dialog.channel.label).toBe("POS 73");

    await contains("#inari-test-printer").select("42");

    expect("#inari-test-printer").toHaveValue("42");
    expect(dialog.printer.id).toBe(42);
    expect(dialog.channel.label).toBe("POS 42");
});
