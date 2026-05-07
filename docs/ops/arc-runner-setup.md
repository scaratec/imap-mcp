# ARC Runner Setup for BDD Pipeline

The BDD suite requires Docker (for Dovecot and Mock-OAuth fixtures).
It runs on a self-hosted GitHub Actions runner managed by
[actions-runner-controller (ARC)](https://github.com/actions/actions-runner-controller)
on the Scaratec K8s cluster.

## Prerequisites

- Kubernetes cluster with `kubectl` access
- Helm 3
- GitHub App with permissions: `Actions: Read`, `Administration: Read/Write`
  (created at https://github.com/organizations/scaratec/settings/apps)

## 1. Install ARC controller

```bash
helm install arc \
  --namespace arc-systems \
  --create-namespace \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller
```

## 2. Create GitHub App secret

```bash
kubectl create namespace arc-runners

kubectl create secret generic arc-github-secret \
  --namespace arc-runners \
  --from-literal=github_app_id=<APP_ID> \
  --from-literal=github_app_installation_id=<INSTALLATION_ID> \
  --from-file=github_app_private_key=<PATH_TO_PEM>
```

## 3. Deploy Runner Scale Set

Create `arc-runner-values.yaml`:

```yaml
githubConfigUrl: https://github.com/scaratec/imap-mcp
githubConfigSecret: arc-github-secret
containerMode:
  type: dind
maxRunners: 2
minRunners: 0
template:
  spec:
    containers:
      - name: runner
        resources:
          requests:
            cpu: "1"
            memory: "2Gi"
          limits:
            cpu: "2"
            memory: "4Gi"
```

```bash
helm install imap-mcp-runner \
  --namespace arc-runners \
  -f arc-runner-values.yaml \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set
```

## 4. Verify

```bash
kubectl get pods -n arc-runners
# Should show runner pods scaling on demand

# Trigger a BDD run:
# Push to main or open a PR — the bdd.yml workflow picks up `self-hosted`
```

## Troubleshooting

- **Runner not picking up jobs:** Check `kubectl logs -n arc-systems` for
  the controller. Verify the GitHub App installation has access to the repo.
- **Docker-in-Docker fails:** Ensure `containerMode.type: dind` is set.
  The BDD suite starts Dovecot and Mock-OAuth via `docker compose`.
- **Timeouts:** The BDD suite takes ~3-4 minutes. Set `timeout-minutes: 15`
  in the workflow if needed.
