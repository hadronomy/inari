---
status: accepted
---

# Use OIDC for the Odoo workload

Each Odoo company uses one OIDC Organization Workload Identity and short-lived
access tokens for Managed Device Work. OpenBao stores and rotates the client
credential.

Every request binds its database, company, Organization, Site, and target
Agent. The identity cannot enroll Agents, administer the Controller, issue raw
Device Work, or access Zenoh directly.

We rejected database-wide identities and static Controller keys. They weaken
company isolation and increase the impact of a leaked credential.
