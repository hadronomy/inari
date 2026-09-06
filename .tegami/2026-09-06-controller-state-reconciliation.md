---
packages:
  "group:edge": patch
---

### Reconcile managed Print Jobs from signed Agent evidence

The Controller verifies and stores signed Agent State observations. Managed Work
queries now include the physical Print Job state and its original signed envelope.
Valid evidence can recover a lost acceptance reply and stop further dispatch.
Older events cannot replace terminal results.

### Bind managed dispatch to the complete Device Work

The Controller and Agent now verify the same fingerprint for the document,
Device, options, and work deadline. A shorter dispatch credential lifetime does
not shorten the Print Job deadline. Stop new admissions and let pending work
finish or expire before upgrading the Controller and Agent together.
