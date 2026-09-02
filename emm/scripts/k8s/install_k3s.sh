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

# ── 5.1 Включить coredumps ─────────────────────────────────────────────────────
# По дефолту в Astra core_pattern=core (рабочая директория), и пакета
# systemd-coredump может не быть. Если pid 1 упадёт во время install/работы k3s,
# без дампа не разобраться. Стратегия: предпочесть systemd-coredump (даёт backtrace
# через coredumpctl), иначе — простой файловый дамп.
echo "→ Настраиваем coredumps..."
mkdir -p /var/lib/coredump
chmod 1777 /var/lib/coredump
if [[ ! -x /lib/systemd/systemd-coredump && ! -x /usr/lib/systemd/systemd-coredump ]]; then
    echo "  systemd-coredump не установлен, пробую apt..."
    apt-get install -y --no-install-recommends systemd-coredump >/dev/null 2>&1 || true
fi
SD_COREDUMP=$(command -v systemd-coredump 2>/dev/null || true)
[[ -z $SD_COREDUMP && -x /lib/systemd/systemd-coredump ]] && SD_COREDUMP=/lib/systemd/systemd-coredump
[[ -z $SD_COREDUMP && -x /usr/lib/systemd/systemd-coredump ]] && SD_COREDUMP=/usr/lib/systemd/systemd-coredump
if [[ -n $SD_COREDUMP ]]; then
    echo "  → systemd-coredump: $SD_COREDUMP"
    cat > /etc/sysctl.d/50-coredump.conf <<EOF
kernel.core_pattern=|$SD_COREDUMP %P %u %g %s %t 9223372036854775808 %h
kernel.core_uses_pid=1
EOF
else
    echo "  → fallback: файловые дампы в /var/lib/coredump/"
    cat > /etc/sysctl.d/50-coredump.conf <<'EOF'
kernel.core_pattern=/var/lib/coredump/core.%e.%p.%t
kernel.core_uses_pid=1
fs.suid_dumpable=2
EOF
fi
sysctl -p /etc/sysctl.d/50-coredump.conf >/dev/null
echo "  core_pattern: $(cat /proc/sys/kernel/core_pattern)"

# ── 6. Установка k3s ──────────────────────────────────────────────────────────
# Зеркала: пробуем github → cn-mirror. На корп-сетях бывает каждое из двух.
# На VM с заблокированным exit-IP github может отдать 403 / TLS-error /
# таймаут — тогда автоматически уходим на rancher-mirror.rancher.cn (стабилен
# в РФ, но иногда сам флакает). Если задан INSTALL_K3S_MIRROR — пробуем
# только его, без fallback'а (для отладки).
#
# SKIP_ENABLE/SKIP_START: на Astra SE 1.8.5 (systemd 252.39-1~deb12u1astra.se3+ci5)
# inline-цепочка `daemon-reload → enable k3s` внутри install.sh воспроизводимо
# валит pid 1 по SIGABRT в startswith()/manager_load_unit() (libsystemd-shared).
# Coredump лежит в /var/lib/systemd/coredump/. Воркэраунд — пропустить
# systemctl-команды в установщике и выполнить их отдельным шагом (§7).
if command -v k3s >/dev/null 2>&1; then
    echo "→ k3s уже установлен: $(k3s --version | head -1)"
else
    if [[ -n "${INSTALL_K3S_MIRROR:-}" ]]; then
        MIRRORS=("$INSTALL_K3S_MIRROR")
    else
        MIRRORS=("github" "cn")
    fi
    K3S_OK=0
    for mirror in "${MIRRORS[@]}"; do
        echo "→ Пробуем mirror=$mirror..."
        # Сам get.k3s.io хостится на GitHub, поэтому для cn-mirror'а скачиваем
        # установщик с rancher-mirror; для github — с канонического URL.
        if [[ "$mirror" == "cn" ]]; then
            INSTALLER_URL="https://rancher-mirror.rancher.cn/k3s/k3s-install.sh"
        else
            INSTALLER_URL="https://get.k3s.io"
        fi
        if curl -sfL "$INSTALLER_URL" | \
            INSTALL_K3S_MIRROR="$mirror" \
            INSTALL_K3S_SKIP_ENABLE=true \
            INSTALL_K3S_SKIP_START=true \
            INSTALL_K3S_EXEC="server --write-kubeconfig-mode=644" \
            sh -; then
            echo "  ✓ k3s установлен через mirror=$mirror"
            K3S_OK=1
            break
        else
            echo "  ✗ mirror=$mirror не сработал (exit $?), пробую следующий..."
        fi
    done
    if [[ "$K3S_OK" -ne 1 ]]; then
        echo "ОШИБКА: все mirror'ы (${MIRRORS[*]}) не сработали." >&2
        exit 1
    fi
fi

# ── 6.1 Enable + start k3s (отдельно, после daemon-reexec) ─────────────────────
echo "→ systemctl daemon-reexec (чтобы обойти Astra-баг в manager_load_unit)..."
systemctl daemon-reexec
echo "→ systemctl enable --now k3s..."
systemctl enable --now k3s

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
