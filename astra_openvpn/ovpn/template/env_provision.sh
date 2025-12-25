#!/bin/bash
set -euo pipefail

SYS_KERNEL="${SYS_KERNEL:-$(uname -r)}"
HOSTNAME="${HOSTNAME:-$(hostname)}"

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libffi-dev cpp gcc make libpdp-dev liblzma-dev python3-requests rustc cargo libcurl4-gnutls-dev strace pkg-config
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libsqlite3-dev wget libbz2-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential pkg-config zlib1g-dev libbz2-dev liblzma-dev xz-utils libssl-dev libreadline-dev libsqlite3-dev libffi-dev libncurses5-dev libgdbm-dev libgdbm-compat-dev libnss3-dev libexpat1-dev tk-dev uuid-dev curl wget ca-certificates rustc cargo strace "linux-tools-${SYS_KERNEL}" python3-requests

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iperf libgost-astra iptables tmux

if [ "${HOSTNAME}" = "testvm1" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install astra-openvpn-server sshpass
    echo 'management 0.0.0.0 7505' | sudo tee -a /etc/openvpn/server.conf
else
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install openvpn sshpass iperf

    av="$(cat /etc/astra_version 2>/dev/null || true)"
    if [[ -n "${av}" ]]; then
        for i in $(seq 0 9999); do
            base="/home/u/openvpn/clients_keys/tester${i}"
            cfg="${base}/client.ovpn"
            [[ -f "${cfg}" ]] || continue

            if [[ "${av}" == 1.8* ]]; then
                sed -i 's/grasshopper-cbc/kuznyechik-cbc/g' "${cfg}" || true
                bash -lc "printf '\n%s\n' 'data-ciphers kuznyechik-cbc' 'auth id-tc26-gost3411-12-512' >> '${cfg}'"
            elif [[ "${av}" == 1.7* ]]; then
                bash -lc "printf '\n%s\n' 'ncp-disable' >> '${cfg}'"
            fi
        done
    else
        echo "Внимание: /etc/astra_version не найден или пуст — клиентские конфиги не менялись."
    fi
fi
    

echo "10000 65000" > /proc/sys/net/ipv4/ip_local_port_range

#python
PY_VERSION="3.12.1"
PY_ROOT="/home/u/python"
PY_TARBALL="Python-${PY_VERSION}.tar.xz"
PY_TARBALL_URL="ftp://10.177.103.10/python/${PY_TARBALL}"
PY_SRC_DIR="${PY_ROOT}/Python-${PY_VERSION}"
MAKE_JOBS="${MAKE_JOBS:-$(nproc)}"

sudo mkdir -p "${PY_ROOT}"
cd "${PY_ROOT}"

if [[ -f "${PY_TARBALL}" ]]; then
    if ! xz -t "${PY_TARBALL}"; then
        sudo rm -f "${PY_TARBALL}"
    fi
fi

if [[ ! -f "${PY_TARBALL}" ]]; then
    sudo wget --tries=5 --timeout=30 --retry-connrefused -O "${PY_TARBALL}" "${PY_TARBALL_URL}"
fi

xz -t "${PY_TARBALL}"
sudo rm -rf "${PY_SRC_DIR}"
tar -xf "${PY_TARBALL}"
cd "${PY_SRC_DIR}"

./configure --with-ensurepip=install
make -j"${MAKE_JOBS}"
sudo make altinstall
/usr/local/bin/python3.12 -c "import socket, ssl, hashlib"

rm -rf "${PY_SRC_DIR}/venv"
/usr/local/bin/python3.12 -m venv "${PY_SRC_DIR}/venv"
"${PY_SRC_DIR}/venv/bin/python" -m pip install --upgrade pip
"${PY_SRC_DIR}/venv/bin/python" -m pip install "allta==1.0.20" -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10
