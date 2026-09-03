---
packages:
  "group:edge": patch
---

### Add secure Managed Work admission

The Controller now accepts preflighted, encrypted Report PDF and Label Document
work from an authorized Odoo backend. Agents publish a separate encryption key
during enrollment, so report content stays encrypted outside the target Agent.
