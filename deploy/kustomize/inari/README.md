# Kustomize deployment

This overlay inflates the local Inari chart with Kustomize's built-in Helm
generator. It is useful for GitOps systems that make Kustomize—not Helm—the
owner of the resulting Kubernetes objects.

Render it with:

```sh
kustomize build --enable-helm deploy/kustomize/inari
```

The overlay deliberately sets `migrations.helmHook=false`. Kustomize and
`kubectl apply` do not implement Helm hook lifecycle semantics, so the
migration becomes an ordinary Job whose name contains the chart version. The
embedded migrator is concurrency-safe and a repeated no-op is harmless.

Do not install the same release with both Helm and Kustomize. Choose one owner
for the object lifecycle. Before applying this overlay, replace the example
URLs and create the referenced Secrets through your normal secret-management
controller. The complete production procedure and required Secret keys are in
[`docs/kubernetes.md`](../../../docs/kubernetes.md).
