#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
readonly CHART="${ROOT}/deploy/helm/inari"
readonly KUSTOMIZATION="${ROOT}/deploy/kustomize/inari"
readonly KIND_NODE_IMAGE="kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5"
readonly CLUSTER_NAME="inari-validation-${PPID}"

workspace="$(mktemp -d)"
cleanup() {
  kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  rm -rf "${workspace}"
}
trap cleanup EXIT

for tool in docker helm kind kubectl kustomize; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf 'missing required tool: %s (run: mise install)\n' "${tool}" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  printf 'Docker is not available; kind API-server validation cannot run.\n' >&2
  exit 1
fi

kind create cluster \
  --name "${CLUSTER_NAME}" \
  --image "${KIND_NODE_IMAGE}" \
  --wait 120s

kubectl create namespace inari
kubectl label namespace inari \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/audit-version=latest \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/warn-version=latest

helm template inari "${CHART}" \
  --namespace inari \
  --kube-version 1.36.1 \
  >"${workspace}/helm.yaml"
kubectl apply \
  --namespace inari \
  --server-side \
  --dry-run=server \
  --filename "${workspace}/helm.yaml"

kustomize build --enable-helm "${KUSTOMIZATION}" >"${workspace}/kustomize.yaml"
kubectl apply \
  --namespace inari \
  --server-side \
  --dry-run=server \
  --filename "${workspace}/kustomize.yaml"

printf 'Kubernetes API-server validation passed.\n'
