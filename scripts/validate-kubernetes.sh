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
    --values "${CHART}/ci/external-config-values.yaml"
done
ct lint --config "${ROOT}/deploy/helm/ct.yaml" --charts "${CHART}"

if helm lint --strict "${CHART}" --set unexpectedValue=true >"${workspace}/invalid-values.log" 2>&1; then
  printf 'values.schema.json accepted an unknown top-level value\n' >&2
  exit 1
fi

if helm template inari "${CHART}" \
  --set database.minConnections=64 \
  --set database.maxConnections=8 \
  >"${workspace}/invalid-database.log" 2>&1; then
  printf 'chart accepted inverted database connection limits\n' >&2
  exit 1
fi

if helm template inari "${CHART}" \
  --set zenoh.config.accessControl.enabled=false \
  >"${workspace}/invalid-zenoh-acl.log" 2>&1; then
  printf 'chart accepted generated Zenoh configuration without access control\n' >&2
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
  --show-only templates/configmap.yaml \
  | yq --unwrapScalar '.data["inari-server.toml"]' \
  >"${workspace}/inari-server.toml"
test -s "${workspace}/inari-server.toml"

helm template inari "${CHART}" \
  --namespace inari \
  --show-only templates/zenoh-configmap.yaml \
  | yq --unwrapScalar '.data["config.json5"]' \
  >"${workspace}/zenoh.json"
jq empty "${workspace}/zenoh.json"

kustomize build --enable-helm "${KUSTOMIZATION}" >"${workspace}/kustomize.yaml"
kubeconform \
  -strict \
  -summary \
  -kubernetes-version "${CURRENT_KUBERNETES_VERSION}" \
  "${workspace}/kustomize.yaml"

helm package "${CHART}" --destination "${workspace}"
test -s "${workspace}/inari-$(helm show chart "${CHART}" | awk '/^version:/ { print $2 }').tgz"

printf 'Kubernetes distribution validation passed.\n'
