# Kustomize deployment

This overlay uses Kustomize’s Helm generator to render the local Inari chart. It
is intended for GitOps platforms that make Kustomize the lifecycle owner of the
resulting Kubernetes objects.

```sh
kustomize build --enable-helm deploy/kustomize/inari
```

The overlay disables Helm hooks. Database migration is therefore an ordinary
Job whose name includes the chart version. Repeated or concurrent Jobs are safe:
the embedded migrator serializes through a PostgreSQL advisory lock.

Replace the example public URLs, OIDC issuer, Zenoh endpoint, and NetworkPolicy
rules before applying the output. Create every referenced Secret through the
environment’s normal secret controller.

Do not install the same release with both Helm and Kustomize. If your GitOps
platform already supports Helm releases, use that native owner unless there is
a concrete reason to manage rendered objects instead.

The full values contract and production procedure are in the
[Kubernetes operations guide](../../../docs/kubernetes.md).
