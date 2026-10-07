from copy import deepcopy

import pytest

from inari_odoo_services.http_client import RemoteServiceError
from inari_odoo_services.inventory import inventory
from inari_odoo_services.workload_identity import WorkloadScope


SCOPE = WorkloadScope("odoo", "1", "org_mizona")


def snapshot():
    return {
        "scope": {
            "database": "odoo",
            "company_id": "1",
            "organization_id": "org_mizona",
        },
        "organization_name": "MIZONA ECOLÓGICA",
        "observed_at": "2026-10-07T00:00:00Z",
        "sites": [{"site_id": "site_store", "name": "MIZONA"}],
        "agents": [
            {
                "agent_id": "agt_counter",
                "site_id": "site_store",
                "health": {"state": "online", "last_seen_at": "2026-10-07T00:00:00Z"},
            }
        ],
        "devices": [
            {
                "device_id": "dev_pos80",
                "agent_id": "agt_counter",
                "site_id": "site_store",
                "display_name": "POS-80",
                "kind": "printer",
                "device_class": "physical",
                "state": "online",
                "transport": "spooler",
                "capabilities": [],
                "last_seen_at": "2026-10-07T00:00:00Z",
            }
        ],
    }


def test_inventory_preserves_scope_and_does_not_invent_business_capabilities():
    value = inventory(snapshot(), SCOPE)
    assert value.organization_name == "MIZONA ECOLÓGICA"
    assert value.devices[0].name == "POS-80"
    assert value.devices[0].state == "online"
    assert not hasattr(value.devices[0], "capabilities")
    assert value.observed_at.tzinfo is None


@pytest.mark.parametrize(
    "field,value",
    [("database", "other"), ("company_id", "2"), ("organization_id", "org_other")],
)
def test_inventory_rejects_another_workload_scope(field, value):
    document = snapshot()
    document["scope"][field] = value
    with pytest.raises(RemoteServiceError, match="another company"):
        inventory(document, SCOPE)


@pytest.mark.parametrize(
    "resource,field,value",
    [
        ("agents", "site_id", "site_other"),
        ("devices", "agent_id", "agt_other"),
        ("devices", "site_id", "site_other"),
        ("devices", "transport", "unknown"),
        ("devices", "state", "ready"),
        ("devices", "last_seen_at", "2026-10-07T00:00:00"),
    ],
)
def test_invalid_inventory_is_rejected_before_projection_writes(resource, field, value):
    document = snapshot()
    document[resource][0][field] = value
    with pytest.raises(RemoteServiceError):
        inventory(document, SCOPE)


@pytest.mark.parametrize("resource", ["sites", "agents", "devices"])
def test_inventory_rejects_duplicate_and_unbounded_lists(resource):
    document = snapshot()
    document[resource].append(deepcopy(document[resource][0]))
    with pytest.raises(RemoteServiceError, match="repeats"):
        inventory(document, SCOPE)
    document[resource] = document[resource] * 1001
    with pytest.raises(RemoteServiceError, match="limits"):
        inventory(document, SCOPE)


def test_device_identity_is_scoped_to_its_agent():
    document = snapshot()
    agent = deepcopy(document["agents"][0])
    agent["agent_id"] = "agt_second"
    document["agents"].append(agent)
    device = deepcopy(document["devices"][0])
    device["agent_id"] = "agt_second"
    document["devices"].append(device)
    assert len(inventory(document, SCOPE).devices) == 2


def test_empty_inventory_is_a_valid_complete_snapshot():
    document = snapshot()
    document.update(sites=[], agents=[], devices=[])
    value = inventory(document, SCOPE)
    assert value.sites == value.agents == value.devices == ()


def test_device_display_name_uses_the_controller_utf8_byte_limit():
    document = snapshot()
    document["devices"][0]["display_name"] = "é" * 512
    assert inventory(document, SCOPE).devices[0].name == "é" * 512
    document["devices"][0]["display_name"] += "x"
    with pytest.raises(RemoteServiceError, match="name is invalid"):
        inventory(document, SCOPE)
