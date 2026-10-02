---
packages:
  "group:edge": patch
---

### Preserve receipt recovery across POS reloads

Completed receipt copies leave the active recovery list. A content-free journal preserves their Print Intent for 90 days. Recovery uses the original Agent channel after a Device Binding changes. Authorized clients can query a Print Job by its durable identifier.
