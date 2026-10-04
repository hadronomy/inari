"""Check admission, identity isolation, and startup dependencies in rendered charts."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


def check(manifest: list[dict], namespace: str) -> None:
    def resource(kind: str, component: str) -> dict:
        matches = [
            item
            for item in manifest
            if item["kind"] == kind
            and item["metadata"].get("labels", {}).get("app.kubernetes.io/component")
            == component
        ]
        assert len(matches) == 1, (kind, component, len(matches))
        return matches[0]

    controller = resource("Deployment", "controller")
    statefulset = resource("StatefulSet", "zenoh")
    controller_config = tomllib.loads(
        resource("ConfigMap", "controller")["data"]["inari-server.toml"]
    )
    policy = controller_config["managed_gateway"]["router_policy"]
    configs = resource("ConfigMap", "zenoh")["data"]
    count = statefulset["spec"]["replicas"]
    assert len(policy["routers"]) == len(configs) == count
    assert len({router["router_id"] for router in policy["routers"]}) == count
    assert len({router["address"] for router in policy["routers"]}) == count
    assert controller["spec"]["strategy"] == {"type": "Recreate"}

    pod = statefulset["spec"]["template"]["spec"]
    container = pod["containers"][0]
    controller_pod = controller["spec"]["template"]["spec"]
    controller_container = controller_pod["containers"][0]
    ports = {port["name"]: port["containerPort"] for port in container["ports"]}
    assert ports["management"] != ports["zenoh-tls"]
    for probe in ("startupProbe", "livenessProbe"):
        assert container[probe]["tcpSocket"]["port"] == "management"
    assert container["readinessProbe"]["tcpSocket"]["port"] == "zenoh-tls"
    assert container["args"] == [
        "--config",
        "/etc/inari-router/router-$(POD_INDEX).toml",
    ]
    assert container["env"] == [
        {
            "name": "POD_INDEX",
            "valueFrom": {
                "fieldRef": {
                    "fieldPath": "metadata.labels['apps.kubernetes.io/pod-index']"
                }
            },
        }
    ]
    assert (
        pod["securityContext"]["runAsUser"]
        == pod["securityContext"]["fsGroup"]
        == 65532
    )
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert pod["automountServiceAccountToken"] is False
    assert statefulset["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "data"
    assert not any(volume.get("name") == "data" for volume in pod["volumes"])

    services = [
        item
        for item in manifest
        if item["kind"] == "Service"
        and item["metadata"]["labels"].get("app.kubernetes.io/component") == "zenoh"
    ]
    headless = next(
        item for item in services if item["spec"].get("clusterIP") == "None"
    )
    public = next(item for item in services if item is not headless)
    assert headless["spec"]["publishNotReadyAddresses"] is True
    assert {port["name"] for port in headless["spec"]["ports"]} == {
        "management",
        "zenoh-tls",
    }
    assert {port["name"] for port in public["spec"]["ports"]} == {"zenoh-tls"}
    peers = {
        f"tls/{statefulset['metadata']['name']}-{ordinal}.{headless['metadata']['name']}.{namespace}.svc.cluster.local:{ports['zenoh-tls']}"
        for ordinal in range(count)
    }

    def mounted_secret(spec: dict, name: str) -> str:
        return next(
            volume["secret"]["secretName"]
            for volume in spec["volumes"]
            if volume["name"] == name
        )

    secrets = [
        mounted_secret(pod, name) for name in ("tls", "management", "policy-public-key")
    ]
    secrets.extend(
        mounted_secret(controller_pod, name)
        for name in ("zenoh-tls", "router-management", "router-policy")
    )
    assert len(secrets) == len(set(secrets)) == 6

    def require_mount(spec: dict, target: dict, path: str) -> None:
        mount = next(
            item
            for item in target["volumeMounts"]
            if path.startswith(item["mountPath"] + "/") and item["name"] != "config"
        )
        assert mount["readOnly"] is True
        assert "subPath" not in mount
        volume = next(item for item in spec["volumes"] if item["name"] == mount["name"])
        assert path.removeprefix(mount["mountPath"] + "/") in {
            item["path"] for item in volume["secret"]["items"]
        }

    for key in (
        "signing_key_file",
        "management_ca_file",
        "management_certificate_file",
        "management_private_key_file",
    ):
        require_mount(controller_pod, controller_container, policy[key])
    for ordinal, address in enumerate(policy["routers"]):
        config = tomllib.loads(configs[f"router-{ordinal}.toml"])
        router = config["router"]
        management = config["management"]
        assert config["fleet_id"] == policy["fleet_id"]
        assert re.fullmatch(r"[a-f0-9]{32}", router["id"])
        assert address["router_id"] == router["id"]
        origin = urlsplit(address["address"])
        assert (
            origin.scheme == "https"
            and origin.path == "/"
            and origin.port == ports["management"]
        )
        own_peer = f"tls/{origin.hostname}:{ports['zenoh-tls']}"
        assert router["probe_endpoint"] == own_peer
        assert set(router["peer_endpoints"]) == peers - {own_peer}
        assert router["listen_endpoint"] == f"tls/[::]:{ports['zenoh-tls']}"
        assert management["address"] == f"0.0.0.0:{ports['management']}"
        assert (
            management["controller_common_name"]
            not in policy["trusted_peer_common_names"]
        )
        assert config["state_directory"] == "/var/lib/inari-router/policy"
        assert config["zenoh_executable"] == "/usr/local/bin/zenohd"
        require_mount(pod, container, config["signing_public_key_file"])
        for key in ("certificate_file", "private_key_file", "client_ca_file"):
            require_mount(pod, container, management[key])
        for key in (
            "root_ca_file",
            "certificate_file",
            "private_key_file",
            "probe_certificate_file",
            "probe_private_key_file",
        ):
            require_mount(pod, container, router[key])

    network = resource("NetworkPolicy", "zenoh")["spec"]
    management_ingress = [
        rule
        for rule in network["ingress"]
        if any(port["port"] == ports["management"] for port in rule["ports"])
    ]
    assert len(management_ingress) == 1
    assert management_ingress[0]["from"] == [
        {"podSelector": controller["spec"]["selector"]}
    ]
    egress = resource("NetworkPolicy", "controller")["spec"]["egress"]
    assert any(
        rule.get("to") == [{"podSelector": statefulset["spec"]["selector"]}]
        and {port["port"] for port in rule["ports"]} == set(ports.values())
        for rule in egress
    )


if __name__ == "__main__":
    check(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
    print("Rendered Router admission contracts passed.")
