---
packages:
  "group:edge": patch
---

### Recover interrupted POS prints safely

Odoo POS now keeps content-free print recovery tasks across reloads. Operators
can check Agent state, retry exact same-tab content, use browser print for a
customer receipt, or explicitly continue without a ticket. Preparation orders
do not advance until the Agent accepts the ticket or the operator dismisses the
recovery task.
