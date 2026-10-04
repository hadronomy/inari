---
packages:
  group:edge:
    replay:
      - exit-prerelease(cargo:inari-agent-client)
      - exit-prerelease(cargo:inari-device-center)
      - exit-prerelease(pip:inari)
      - exit-prerelease(pip:inari-brand)
      - exit-prerelease(pip:inari-print-contracts)
      - exit-prerelease(msix:inari-device-center)
---

### Continue setup after an Agent restart

Device Center now provides a Restart Agent action when an invitation needs a
runtime restart. On Windows, the service host applies the saved setup through a
package-verified local request. Standard users need no service-control permission
for this action. Device Center then shows connection progress and Device selection.
Failed restarts remain available for retry. A connection check that stops offers
Check again.
Native service requests stop after ten seconds if the service does not reply.

Setup keeps Device access blocked until the Agent reports completion.
Setup completion refreshes the operations window and tray. Closing setup hides
the window, and the tray restores it with its current progress. Service actions
wait for completion and reject concurrent requests from another window.
