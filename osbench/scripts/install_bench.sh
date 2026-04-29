#!/bin/bash
set -vx

install_deps() {
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y libtirpc-dev
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y libtirpc-devel
    elif command -v pacman &> /dev/null; then
        sudo pacman -Syu --noconfirm base-devel libtirpc
    else
        echo "Не найден пакетный менеджер"
        exit 1
    fi
}

install_perf() {
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y linux-tools-common linux-tools-$(uname -r) || sudo apt-get install -y linux-tools-common
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
    make -j"$(nproc)" || { echo "❌ Ошибка сборки LMbench"; exit 1; }
    popd
    echo "LMBench собран."
}

build_unixbench() {
    pushd benchmarks/UnixBench/byte-unixbench/UnixBench || { echo "❌ Папка не найдена"; exit 1; }
    chmod +x pgms/*.sh
    chmod +x Run
    sudo locale-gen en_US.UTF-8
    sudo update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
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