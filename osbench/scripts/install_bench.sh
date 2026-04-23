#!/bin/bash
set -e  

install_deps() {
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y build-essential git libtirpc-dev
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y make gcc git libtirpc-devel
    elif command -v pacman &> /dev/null; then
        sudo pacman -Syu --noconfirm base-devel git libtirpc
    else
        echo "Package manager not supported."
        exit 1
    fi
}

install_perf() {
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y linux-tools-common linux-tools-$(uname -r) || \
        sudo apt-get install -y linux-tools-common
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y perf
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm perf
    else
        echo "Cannot install perf automatically. Install it manually."
    fi
}

build_lmbench() {
    if [ ! -d /opt/lmbench ]; then
        cd /opt || exit
        git clone https://github.com/intel/lmbench.git
        cd lmbench || exit
        mkdir -p ./SCCS && touch ./SCCS/s.ChangeSet
        make -j"$(nproc)"
        echo "LMBench built."
    else
        echo "LMBench already exists."
    fi
}

build_unixbench() {
    if [ ! -d /opt/byte-unixbench ]; then
        cd /opt || exit
        git clone https://github.com/kdlucas/byte-unixbench.git
        cd byte-unixbench/UnixBench || exit
        make -j"$(nproc)"
        echo "UnixBench built."
    else
        echo "UnixBench already exists."
    fi
}

main() {
    install_deps
    install_perf
    build_lmbench
    build_unixbench
    echo "All benchmarks ready."
}

main "$@"