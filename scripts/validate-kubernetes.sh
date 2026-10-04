#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
readonly CHART="${ROOT}/deploy/helm/inari"
readonly KUSTOMIZATION="${ROOT}/deploy/kustomize/inari"
readonly MINIMUM_KUBERNETES_VERSION="1.29.0"
readonly CURRENT_KUBERNETES_VERSION="1.36.2"

workspace="$(mktemp -d)"
trap 'rm -rf "${workspace}"' EXIT

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'missing required tool: %s (run: mise install)\n' "$1" >&2
    exit 1
  fi
}

for tool in actionlint ct helm jq kubeconform kube-linter kustomize shellcheck yamllint yq; do
  require_tool "${tool}"
done

shellcheck "${ROOT}/scripts/validate-kubernetes.sh" "${ROOT}/scripts/validate-kubernetes-server.sh"
actionlint "${ROOT}"/.github/workflows/*.yaml
yamllint --config-file "${ROOT}/deploy/helm/yamllint.yaml" \
  "${CHART}/Chart.yaml" \
  "${CHART}/values.yaml" \
  "${CHART}/ci" \
  "${KUSTOMIZATION}"

for helm_version in 3.21.3 4.2.3; do
  mise exec "helm@${helm_version}" -- helm lint --strict "${CHART}"
  mise exec "helm@${helm_version}" -- helm lint --strict "${CHART}" \
    --values "${CHART}/ci/ingress-autoscaling-values.yaml"
done
ct lint --config "${ROOT}/deploy/helm/ct.yaml" --charts "${CHART}"

helm template inari "${CHART}" --namespace inari \
  --values "${CHART}/ci/database-ca-values.yaml" \
  >"${workspace}/database-ca.yaml"
for kind in Deployment Job; do
  yq --output-format json --no-doc \
    "select(.kind == \"${kind}\") | .spec.template.spec" \
    "${workspace}/database-ca.yaml" \
    | jq --exit-status '
        any(.volumes[]; .name == "database-ca"
          and .secret.secretName == "inari-database-ca"
          and .secret.items == [{"key": "ca.crt", "path": "ca.crt"}])
        and any(.containers[0].volumeMounts[];
          .name == "database-ca"
          and .mountPath == "/var/run/secrets/inari/database-ca"
          and .readOnly == true)
      ' >/dev/null
done
kubeconform -strict -summary -kubernetes-version "${CURRENT_KUBERNETES_VERSION}" \
  "${workspace}/database-ca.yaml"
kube-linter lint "${workspace}/database-ca.yaml"

if helm lint --strict "${CHART}" --set unexpectedValue=true >"${workspace}/invalid-values.log" 2>&1; then
  printf 'values.schema.json accepted an unknown top-level value\n' >&2
  exit 1
fi

if helm template inari "${CHART}" \
  --set-string image.tag= \
  --set-string image.digest= \
  >"${workspace}/missing-controller-image.log" 2>&1; then
  printf 'chart accepted an empty Controller image selection\n' >&2
  exit 1
fi

if helm template inari "${CHART}" \
  --set database.minConnections=64 \
  --set database.maxConnections=8 \
  >"${workspace}/invalid-database.log" 2>&1; then
  printf 'chart accepted inverted database connection limits\n' >&2
  exit 1
fi

reject_managed_values() {
  local case_name="$1"
  shift
  if helm template inari "${CHART}" \
    --values "${CHART}/ci/managed-work-values.yaml" \
    "$@" >"${workspace}/invalid-${case_name}.log" 2>&1; then
    printf 'chart accepted invalid managed values: %s\n' "${case_name}" >&2
    exit 1
  fi
}

reject_managed_values obsolete-router-config --set zenoh.config.existingConfigMap=unsafe
reject_managed_values no-trusted-peers --set-json 'managedGateway.routerPolicy.trustedPeerCommonNames=[]'
reject_managed_values agent-as-trusted-peer --set-json 'managedGateway.routerPolicy.trustedPeerCommonNames=["agt_0123456789abcdef01234567"]'
reject_managed_values no-persistence --set zenoh.persistence.enabled=false
reject_managed_values no-router-image --set-string zenoh.image.tag= --set-string zenoh.image.digest=
reject_managed_values shared-management-identity --set zenoh.management.controllerSecret.name=inari-controller-zenoh-tls
reject_managed_values shared-management-common-name --set zenoh.management.controllerCommonName=inari-controller
reject_managed_values colliding-ports --set zenoh.management.port=7447
reject_managed_values no-signing-secret --set-string managedGateway.routerPolicy.signingKey.name=
reject_managed_values too-many-routers --set zenoh.replicas=33

if helm template inari "${CHART}" \
  --values "${CHART}/ci/managed-work-values.yaml" \
  --set-string managedGateway.certificate.stepCa.rootFingerprint=invalid \
  >"${workspace}/invalid-ca-pin.log" 2>&1; then
  printf 'chart accepted step-ca without a SHA-256 root fingerprint\n' >&2
  exit 1
fi

if helm template inari "${CHART}" \
  --values "${CHART}/ci/managed-work-values.yaml" \
  --set server.maxBodySizeBytes=1048576 \
  >"${workspace}/invalid-managed-body-limit.log" 2>&1; then
  printf 'chart accepted managed dispatch with an insufficient request body limit\n' >&2
  exit 1
fi

if helm template inari "${CHART}" \
  --values "${CHART}/ci/managed-work-values.yaml" \
  --set managedGateway.enabled=false \
  >"${workspace}/invalid-managed-dispatch.log" 2>&1; then
  printf 'chart accepted managed dispatch with the managed gateway disabled\n' >&2
  exit 1
fi

for kubernetes_version in "${MINIMUM_KUBERNETES_VERSION}" "${CURRENT_KUBERNETES_VERSION}"; do
  manifest="${workspace}/helm-${kubernetes_version}.yaml"
  helm template inari "${CHART}" \
    --namespace inari \
    --kube-version "${kubernetes_version}" \
    >"${manifest}"
  kubeconform \
    -strict \
    -summary \
    -kubernetes-version "${kubernetes_version}" \
    "${manifest}"
done

kube-linter lint "${workspace}/helm-${CURRENT_KUBERNETES_VERSION}.yaml"

helm template inari "${CHART}" \
  --namespace inari \
  --values "${CHART}/ci/managed-work-values.yaml" \
  --kube-version "${CURRENT_KUBERNETES_VERSION}" \
  >"${workspace}/managed-work.yaml"
kubeconform -strict -summary -kubernetes-version "${CURRENT_KUBERNETES_VERSION}" \
  "${workspace}/managed-work.yaml"
kube-linter lint "${workspace}/managed-work.yaml"

helm template inari "${CHART}" \
  --namespace inari \
  --show-only templates/configmap.yaml \
  | yq --unwrapScalar '.data["inari-server.toml"]' \
  >"${workspace}/inari-server.toml"
test -s "${workspace}/inari-server.toml"

helm template inari "${CHART}" \
  --namespace inari \
  --values "${CHART}/ci/oidc-audiences-values.yaml" \
  --show-only templates/configmap.yaml \
  | yq --unwrapScalar '.data["inari-server.toml"]' \
  >"${workspace}/oidc-audiences.toml"
python3 - "${workspace}/inari-server.toml" "${workspace}/oidc-audiences.toml" <<'PYTHON'
import sys
import tomllib

for path, audiences in zip(sys.argv[1:], ([], ["trusted-project"]), strict=True):
    with open(path, "rb") as source:
        oidc = tomllib.load(source)["identity"]["oidc"]
    assert oidc["additional_id_token_audiences"] == audiences
PYTHON

helm template inari "${CHART}" \
  --namespace inari \
  --values "${CHART}/ci/managed-work-values.yaml" \
  --show-only templates/configmap.yaml \
  | yq --unwrapScalar '.data["inari-server.toml"]' \
  >"${workspace}/managed-server.toml"
python3 - "${workspace}/inari-server.toml" "${workspace}/managed-server.toml" <<'PYTHON'
import sys
import tomllib

for path, enabled in zip(sys.argv[1:], (False, True), strict=True):
    with open(path, "rb") as source:
        managed = tomllib.load(source)["managed_gateway"]
    assert managed["dispatch"]["enabled"] is enabled
    assert ("managed_work:dispatch" in managed["controller_actions"]) is enabled
    if not enabled:
        assert managed["enabled"] is False
        assert managed["onboarding"]["enabled"] is False
        assert managed["certificate"]["mode"] == "none"
PYTHON

yq --output-format json --no-doc '.' "${workspace}/managed-work.yaml" \
  | jq --slurp '.' >"${workspace}/managed-work.json"
python3 "${ROOT}/scripts/check-router-chart.py" "${workspace}/managed-work.json" inari

for router_count in 1 12 32; do
  helm template contract "${CHART}" --namespace router-contract \
    --values "${CHART}/ci/managed-work-values.yaml" \
    --set zenoh.replicas="${router_count}" \
    | yq --output-format json --no-doc '.' \
    | jq --slurp '.' >"${workspace}/router-count-${router_count}.json"
  python3 "${ROOT}/scripts/check-router-chart.py" \
    "${workspace}/router-count-${router_count}.json" router-contract
done

kustomize build --enable-helm "${KUSTOMIZATION}" >"${workspace}/kustomize.yaml"
kubeconform \
  -strict \
  -summary \
  -kubernetes-version "${CURRENT_KUBERNETES_VERSION}" \
  "${workspace}/kustomize.yaml"

helm package "${CHART}" --destination "${workspace}"
test -s "${workspace}/inari-$(helm show chart "${CHART}" | awk '/^version:/ { print $2 }').tgz"

printf 'Kubernetes distribution validation passed.\n'
