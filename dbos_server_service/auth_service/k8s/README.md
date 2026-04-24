# Kubernetes manifests

This directory contains baseline production manifests for `auth_service`.

Included resources:

- `namespace.yaml`
- `configmap.yaml`
- `secret.example.yaml`
- `deployment.yaml`
- `service.yaml`
- `ingress.yaml`
- `hpa.yaml`
- `pdb.yaml`

Notes:

- The PostgreSQL cluster is expected to be managed separately.
- Replace `your-registry/auth_service:latest` with the real image.
- Copy `secret.example.yaml` to a real secret manifest or use your secret manager.
- Update the ingress host for your environment.
