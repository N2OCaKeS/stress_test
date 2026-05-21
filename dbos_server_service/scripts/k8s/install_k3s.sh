#!/usr/bin/env bash
# Установка k3s на Astra Linux SE (и совместимых Debian-based).
#
# Идемпотентный: можно перезапускать.
# Запускать на VM как root или через sudo.

set -euo pipefail

# ── 0. Проверка root ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ОШИБКА: нужны права root. Запустите через sudo." >&2
    exit 1
fi

echo "→ Проверка ОС..."
if [[ -f /etc/astra_version ]]; then
    echo "  Astra Linux: $(cat /etc/astra_version)"
elif [[ -f /etc/os-release ]]; then
    . /etc/os-release
    echo "  $PRETTY_NAME"
fi

# ── 1. parsec (Astra SE) — предупреждение ──────────────────────────────────────
# parsec MAC может блокировать overlayfs/namespace. Для k3s нужен уровень 0
# или специальная настройка allowlist. Эта проверка не блокирующая.
if command -v pdpl-user >/dev/null 2>&1; then
    parsec_lvl=$(grep -E "^MAC_LEVEL" /etc/parsec/mac/macctl.conf 2>/dev/null | awk '{print $2}' || echo "0")
    if [[ "$parsec_lvl" != "0" ]]; then
        echo "⚠ ВНИМАНИЕ: parsec MAC уровень = $parsec_lvl. k3s может не запуститься."
        echo "  Рекомендация: для VM с k3s оставьте уровень 0."
        echo "  Продолжать? (Ctrl+C для выхода, Enter для продолжения)"
        read -r
    fi
fi

# ── 2. Отключить swap (требование k8s) ────────────────────────────────────────
echo "→ Отключаем swap..."
swapoff -a
sed -i.bak '/\sswap\s/s/^/#/' /etc/fstab

# ── 3. Загрузить нужные модули ядра ───────────────────────────────────────────
echo "→ Загружаем kernel modules: br_netfilter, overlay..."
cat > /etc/modules-load.d/k3s.conf <<EOF
br_netfilter
overlay
EOF
modprobe br_netfilter
modprobe overlay

# ── 4. Настроить sysctl ───────────────────────────────────────────────────────
echo "→ Настраиваем sysctl..."
cat > /etc/sysctl.d/k3s.conf <<EOF
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system >/dev/null

# ── 5. Открыть нужные порты в firewall (если ufw активен) ─────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "active"; then
    echo "→ Открываем порты в ufw..."
    ufw allow 6443/tcp comment "k3s API"
    ufw allow 80/tcp   comment "Traefik HTTP (для редиректа на HTTPS)"
    ufw allow 443/tcp  comment "Traefik HTTPS"
fi

# ── 6. Установка k3s ──────────────────────────────────────────────────────────
if command -v k3s >/dev/null 2>&1; then
    echo "→ k3s уже установлен: $(k3s --version | head -1)"
else
    echo "→ Скачиваем и устанавливаем k3s (с встроенным Traefik для Ingress)..."
    curl -sfL https://get.k3s.io | \
        INSTALL_K3S_EXEC="server --write-kubeconfig-mode=644" \
        sh -
fi

# ── 7. Проверка ───────────────────────────────────────────────────────────────
echo "→ Ждём готовности ноды..."
until kubectl get nodes 2>/dev/null | grep -q "Ready"; do
    sleep 2
done

echo ""
echo "✓ k3s установлен и работает:"
kubectl get nodes -o wide
echo ""
echo "  Kubeconfig:  /etc/rancher/k3s/k3s.yaml"
echo "  Чтобы использовать с локальной машины:"
echo "    scp root@<VM>:/etc/rancher/k3s/k3s.yaml ~/.kube/config"
echo "    sed -i 's/127.0.0.1/<VM_IP>/' ~/.kube/config"
echo ""
echo "Дальше: scripts/k8s/gen_secrets.sh, потом deploy.sh"
