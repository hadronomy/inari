---
status: accepted
---

# Broker every Driver resource

One deep Agent Resource Broker Module supplies each Driver worker with checked
Device resources. A worker receives only a connected socket, Device handle, or
bounded spooler operation.

Direct network and filesystem access gives Driver code more authority than its
Device Capability needs. The Resource Broker keeps policy, audit, and
operating-system differences behind one small Interface.

Linux uses `no_new_privs`, seccomp, Landlock, and cgroup v2. Windows uses a
restricted token and Job Object.

Each resource grant belongs to one execution and its deadline. The Resource
Broker revokes it at terminal state or worker exit.

A network grant permits only the endpoint and resolved addresses in the tested
Driver Profile. It rejects unbound, loopback, link-local, metadata, multicast,
and unspecified addresses.

The Resource Broker detects DNS rebinding. Private addresses require an exact
Device Binding and a passed Device Test.

An undeclared request fails with public code `permission_denied`. Restricted
diagnostics record `resource_denied`.

The Agent quarantines that Driver digest, raises a security alert, and keeps
unrelated Drivers active.
