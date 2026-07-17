#!/bin/bash

set -e
export CFLAGS="-Wno-all -Wno-format-overflow -Wno-stringop-truncation"

install_deps() {
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y libtirpc-dev
    elif command -v dnf &> /dev/null; then
        if grep -q "releasever" /etc/yum/vars/releasever 2>/dev/null; then
            sudo dnf install -y 'dnf-command(config-manager)' 2>/dev/null || true
            RHEL_VERSION=$(rpm -E %rhel 2>/dev/null || echo "8")
            sudo dnf config-manager --set-enabled "codeready-builder-for-rhel-${RHEL_VERSION}-$(arch)-rpms" 2>/dev/null || true
            sudo subscription-manager repos --enable "codeready-builder-for-rhel-${RHEL_VERSION}-$(arch)-rpms" 2>/dev/null || true
        fi
        sudo dnf install -y libtirpc-devel || \
        sudo dnf install -y libtirpc || \
        sudo dnf install -y tirpc-devel || \
        { echo "Не удалось установить libtirpc"
          return 1; }
        sudo dnf install -y glibc-static || \
        echo "glibc-static не установлен, сборка fs_mark может не удасться"
        sudo dnf install -y perl perl-core perl-Time-HiRes || \
        echo "Некоторые Perl-модули не установлены, UnixBench может не работать"
    elif command -v pacman &> /dev/null; then
        sudo pacman -Syu --noconfirm base-devel libtirpc
    else
        echo "Не найден пакетный менеджер"
        exit 1
    fi
}

install_perf() {
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y linux-tools-$(uname -r) || sudo apt-get install -y linux-tools-common || \
        sudo apt-get install -y linux-perf-$(uname -r) || sudo apt-get install -y linux-perf
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y perf
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm perf
    else
        echo "Не удалось установить perf"
    fi
}

build_lmbench() { 
    pushd benchmarks/LMbench/lmbench || { echo "❌ Папка не найдена"; exit 1; }
    chmod -R +x scripts/ 2>/dev/null || true
    RPC_FLAGS=""
    if [ -d /usr/include/tirpc/rpc ]; then
        # RHEL/Fedora: rpc.h в /usr/include/tirpc/rpc/
        RPC_FLAGS="-I/usr/include/tirpc -ltirpc"
        echo "   Обнаружен RPC в /usr/include/tirpc (RHEL/Fedora)"
    elif [ -d /usr/include/rpc ]; then
        # Debian/Ubuntu/Astra: rpc.h в /usr/include/rpc/
        RPC_FLAGS=""
        echo "   Обнаружен RPC в /usr/include/rpc (Debian/Ubuntu)"
    else
        echo "   Не удалось найти rpc/rpc.h"
        exit 1
    fi
    make -j"$(nproc)" CFLAGS="-Wno-all -Wno-format-overflow -Wno-stringop-truncation ${RPC_FLAGS}" || {
        echo "❌ Ошибка сборки LMbench"
        exit 1
    }
    popd
    echo "LMBench собран."
}

build_unixbench() {
    pushd benchmarks/UnixBench/byte-unixbench/UnixBench || { echo "❌ Папка не найдена"; exit 1; }
    chmod +x pgms/*.sh
    chmod +x Run
    make -j"$(nproc)" || { echo "❌ Ошибка сборки UnixBench"; exit 1; }
    popd
    echo "UnixBench собран."
}

build_fsmark() {
    pushd benchmarks/fs_mark || { echo "❌ Папка не найдена"; exit 1; }
    make -j"$(nproc)" || { echo "❌ Ошибка сборки FS_Mark"; exit 1; }
    popd
    echo "FS_Mark собран."
}


main() {
    install_deps
    install_perf
    build_lmbench
    build_unixbench
    build_fsmark
    echo "All benchmarks ready."
}

main "$@"