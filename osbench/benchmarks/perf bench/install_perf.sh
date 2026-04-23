#!/bin/bash

install_perf() {
    # Определяем дистрибутив и устанавливаем perf
    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu/Astra...
        sudo apt-get install -y linux-tools-common linux-tools-$(uname -r)
        # fallback, если точный пакет не найден
        if [ $? -ne 0 ]; then
            sudo apt-get install -y linux-tools-common
        fi
    elif command -v dnf &> /dev/null; then
        # Fedora/RHEL/CentOS...
        sudo dnf install -y perf
    elif command -v pacman &> /dev/null; then
        # Arch...
        sudo pacman -S --noconfirm perf
    else
        echo "Unknown package manager. Install 'perf' manually."
        exit 1
    fi
    
    # Проверяем, что perf установлен
    if sudo perf --version &> /dev/null; then
        echo "perf installed successfully: $(sudo perf --version)"
    else
        echo "ERROR: perf not available"
        exit 1
    fi
}
