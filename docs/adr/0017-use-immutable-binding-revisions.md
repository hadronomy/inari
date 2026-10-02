---
status: accepted
---

# Use immutable binding revisions

A logical `inari.device.binding` points to `active_revision_id`. A separate
`inari.device.binding.revision` record is immutable. A Device Manager creates a
revision only when the authorization digest changes, runs its Device Test, and
activates the revision in one transaction.

Activation deactivates the prior revision. Existing Device Work and audit
records retain that revision.

This lifecycle prevents a device change from altering a Retry or historical
record. It also permits Device Tests before a new assignment reaches an active
POS session.

A Retry keeps the original Binding Revision and Device. A Reprint uses the
current active revision after the operator sees the changed Device.

An open POS receives a configuration-change banner. The new revision applies
after the next POS reload and never changes a target during an order.

Activation locks the target POS record. A partial unique index permits one
active assignment for each Device Purpose and target.

Guided flows own assignment, Device Test, and activation. A stale concurrent
activation returns to the changed assignment. It cannot activate an obsolete
Binding Revision.

Each Device Test creates an immutable `inari.device.test.result` record. The
Binding Revision points to `latest_observed_test_id` and
`latest_passed_test_id`. Test history remains available during activation and
later audit.

Printer, drawer, and scanner results expire only when the Device identity,
Binding Revision, Driver Profile digest, or tested capability changes. A scale
result also stops authorizing price calculations at Certification Record
expiry.

`failed_environment` records conditions that prevented a valid result.
`failed_contract` records a proved capability failure. Neither result can
activate an inactive revision. A contract failure on an active revision marks
it `Needs attention` and blocks its Device Purpose.

Each physical check uses `Correct`, `Incorrect`, or `Test did not run`.
Agent evidence remains separate, and the result contains no free text.

An environmental failure keeps the active revision and its latest passed test.
The Device Purpose resumes when the same Device and Driver Profile return to
`Ready`. Identity, profile, binding, certification, or tested-capability
changes require a new test.
