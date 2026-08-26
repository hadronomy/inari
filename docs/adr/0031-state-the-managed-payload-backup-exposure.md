---
status: accepted
---

# State the managed-payload backup exposure

Deleting a live Managed Payload removes its ciphertext and wrapped data key in
one PostgreSQL transaction. Matching PostgreSQL and OpenBao backups can still
recover that content during the 14-day Backup Exposure Window.

Product text states this limit. Managed Payload deletion is immediate in live
systems and final after backup expiry.

One Transit key wraps Managed Payload data keys in each environment. Plaintext
Transit key backup stays disabled. The key rotates every 90 days. Hard key
deletion is a controlled security response.

All backups are encrypted. Every restore records its actor, recovery point,
purpose, and result. Recovery never dispatches restored expired work.
