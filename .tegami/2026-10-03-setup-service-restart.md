---
packages:
  "group:edge": patch
---

### Continue setup after an Agent restart

Device Center now provides a Restart Agent action when an invitation needs a
service restart. It waits for the service to stop and start, then shows
connection progress and Device selection. Failed restarts remain available for
retry. A connection check that stops offers Check again.

Setup keeps Device access blocked until the Agent reports completion.
Setup completion refreshes the operations window and tray. Closing setup hides
the window, and the tray restores it with its current progress. Service actions
wait for completion and reject concurrent requests from another window.
