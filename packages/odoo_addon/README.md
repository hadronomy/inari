# POS IoT Bridge for Odoo 19 Community

This addon provides a community-owned local hardware bridge for Odoo POS. It is designed to replace the paid IoT dependency for the most common local-hardware scenarios:

- thermal receipt printing
- cash drawer pulse
- manual reprint from the receipt screen
- health checks and diagnostics
- plugin-ready agent architecture for scanners, scales, and customer displays

## Install

1. Copy `pos_iot_bridge` into your Odoo custom addons path.
2. Update apps list and install **POS IoT Bridge**.
3. Configure your POS under **Point of Sale -> Configuration -> Point of Sale -> IoT Bridge**.
4. Start the Windows agent from `windows_agent` on each terminal machine.

## Notes

- Payload mode is the production path and uses `order.export_for_printing()` as the source of truth.
- HTML mode is supported by the agent, but payload mode is preferred for predictability and thermal-printer fidelity.
- The JS patches intentionally target the receipt screen and order export path only to keep upgrades low-risk.
