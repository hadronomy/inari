---
packages:
  "group:edge": patch
---

### Retry Print Job observations until the Controller stores them

The Agent now requires a Controller storage receipt for signed Print Job
observations. A connection failure or lost reply keeps the same observation
pending for retry. The upgrade requeues previously sent observations once.
