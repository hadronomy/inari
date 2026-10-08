# Connectivity target language

This glossary defines the generic connectivity target. Current Odoo contracts
retain their existing meanings until implementation changes them explicitly.

## Components

**Agent**:
The long-running Inari service that owns Devices, authorization, and durable Device Work.
_Avoid_: IoT Box, hardware proxy, daemon

**Agent Host**:
The computer that runs Agent and can reach its Devices.
_Avoid_: Controller, POS server

**Device Center**:
The native user-session Client for local Device management, access approval, and Agent support.
_Avoid_: Agent, service, Controller

**Client**:
A native application, browser context, CLI, or integration that requests an Inari operation.
_Avoid_: Agent, operator, SDK

**SDK**:
A library inside a Client that implements the Inari contract and connection lifecycle.
_Avoid_: service, Controller, connector process

**Device Adapter**:
An integration boundary that converts application intent into the generic Inari device contract.
_Avoid_: Driver, hardware proxy

**Driver**:
The Agent component that implements a Device protocol and exposes its Device Capabilities.
_Avoid_: Device Adapter, integration

**Controller**:
An optional service for explicitly delegated fleet policy and coordination.
_Avoid_: relay, Agent, required pairing server

## Device authority

**Device**:
A physical peripheral that Agent identifies with a stable Device identity.
_Avoid_: printer name, device IP

**Device Work**:
One requested Device action, reading, or output under the Inari contract.
_Avoid_: command, payload, transport message

**Device Capability**:
An Agent declaration that a Device and Driver support one class of Device Work.
_Avoid_: device type, transport feature

**Device Health**:
The current Driver observation of Device readiness, with a stable state and reason.
_Avoid_: connection state, work outcome

**Shared Device**:
A Device that Agent can schedule safely for several authorized Clients.
_Avoid_: global permission, shared credential

**Exclusive Device**:
A Device that requires an Agent-owned lease for one authorized use at a time.
_Avoid_: browser lock, POS configuration

## Identity and access

**Client Pairing**:
The approved association between a durable Client identity and one Agent.
_Avoid_: login, transport handshake, Odoo origin binding

**Client Grant**:
Agent-owned permission for a Client to use specified Devices and Device Capabilities within explicit bounds.
_Avoid_: ticket, local token, shared credential

**Pairing Request**:
A time-limited request that binds a proposed Client identity, target Agent, and requested access for local approval.
_Avoid_: login, reusable bearer grant

**Agent Administrator**:
An OS-authorized person with permission to administer Agent through a protected local interface.
_Avoid_: System Administrator, automatically trusted application

**Contract Major**:
The identity that changes when the Inari wire contract makes a breaking change.
_Avoid_: package version, transport version

## Recovery and evidence

**Idempotency Key**:
A stable Client-scoped identity that binds repeated submission to the same Device Work and execution record.
_Avoid_: work ID, retry count

**Agent Boot Identity**:
The unique identity of one Agent runtime start, separate from its persistent Agent identity.
_Avoid_: PID, Agent ID

**Output Evidence**:
The Driver classification of completion evidence: device, spooler, or transport.
_Avoid_: acknowledgement, acceptance, exactly once

**Outcome Unknown**:
A Device Work outcome where physical completion or failure cannot be proved after Device I/O starts.
_Avoid_: Failed, Output Confirmed

**Job Reconciliation**:
Recovery of authoritative execution state after event loss, reconnect, or an uncertain response, without another physical output request.
_Avoid_: resubmit, reprint, transport retry

**Diagnostics Bundle**:
A content-free support record of identity, contracts, Device Health, connections, and operational errors.
_Avoid_: receipt archive, key export, log dump
