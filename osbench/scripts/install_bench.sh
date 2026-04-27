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
    cd benchmarks/LMbench/lmbench || { echo "❌ Папка не найдена"; exit 1; }
    make -j"$(nproc)" || { echo "❌ Ошибка сборки LMbench"; exit 1; }
    echo "LMBench собран."
}

build_unixbench() {
    cd benchmarks/UnixBench/byte-unixbench/UnixBench || { echo "❌ Папка не найдена"; exit 1; }
    make -j"$(nproc)" || { echo "❌ Ошибка сборки UnixBench"; exit 1; }
    echo "UnixBench собран."
}

build_fsmark() {
    cd benchmarks/fs_mark || { echo "❌ Папка не найдена"; exit 1; }
    make -j"$(nproc)" || { echo "❌ Ошибка сборки FS_Mark"; exit 1; }
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