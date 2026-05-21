#!/usr/bin/env bash
# Откат последнего rollout.
# Использование:
#   scripts/k8s/rollback.sh auth      — откат auth_service на предыдущую ревизию
#   scripts/k8s/rollback.sh logging   — откат logging_service
#   scripts/k8s/rollback.sh auth N    — откат auth_service на конкретную ревизию N

set -euo pipefail

TARGET="${1:?usage: rollback.sh auth|logging [revision]}"
REVISION="${2:-}"

case "$TARGET" in
    auth)    DEPLOY="auth-service" ;;
    logging) DEPLOY="logging-service" ;;
    *)       echo "ОШИБКА: цель должна быть 'auth' или 'logging'" >&2; exit 1 ;;
esac

echo "→ История ревизий $DEPLOY:"
kubectl -n dbos rollout history deploy/"$DEPLOY"
echo ""

if [[ -n "$REVISION" ]]; then
    echo "→ Откат на ревизию $REVISION..."
    kubectl -n dbos rollout undo deploy/"$DEPLOY" --to-revision="$REVISION"
else
    echo "→ Откат на предыдущую ревизию..."
    kubectl -n dbos rollout undo deploy/"$DEPLOY"
fi

kubectl -n dbos rollout status deploy/"$DEPLOY" --timeout=300s
echo ""
echo "✓ Rollback завершён."
kubectl -n dbos get pods -l app="$DEPLOY"
