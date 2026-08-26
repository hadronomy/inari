---
status: accepted
---

# Ship Driver code with the Agent artifact

Driver code ships only in the signed Agent artifact. The Agent does not
download Driver code or reload it at runtime.

This choice keeps executable code inside the tested release and supply-chain
contract. A remote Driver marketplace adds a second code-trust path, mixed
runtime versions, and a larger worker-escape risk.

The Agent rollout supplies Driver canaries. Signed Driver Profiles remain
separate data. A profile can change only through its signature, compatibility,
activation, test, and revocation rules.

The artifact contains a signed Driver manifest with code digests, operating
systems, Contract Majors, and profile-schema ranges. Agent readiness blocks an
unlisted or mismatched Driver.

A signature or manifest-digest mismatch blocks complete Agent readiness. A
correctly signed optional Driver load failure affects only its Devices.

The rollout uses a physical canary and one Site for one hour. A security
failure, Contract failure, worker circuit, `outcome_unknown`, or Device failure
rate greater than 1% stops the rollout.

The physical lab covers each changed Driver and hardware family. The release
cannot include an untested changed combination.
