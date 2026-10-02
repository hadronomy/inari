---
packages:
  "group:edge": patch
---

### Publish signed managed Print Job observations

The Agent signs managed Print Job state from its durable journal. Pending
observations survive restart and retain each historical state. Managed replies
stay bound to their original Agent, Organization, and Site after enrollment changes.
