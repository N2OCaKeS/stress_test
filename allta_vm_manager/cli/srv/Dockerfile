ARG BASE_IMAGE=allta.devos.astralinux.ru:21503/cli:latest
FROM ${BASE_IMAGE}
WORKDIR /src

COPY . /src/cli

RUN chmod +x /src/cli/build.sh \
    && printf '%s\n' \
    '#!/bin/bash' \
    'set -euo pipefail' \
    '' \
    'cd /src/cli' \
    'export ALLTA_BUILDER_PYTHON="${ALLTA_BUILDER_PYTHON:-/opt/allta-builder/python}"' \
    './build.sh allta_vm' \
    '' \
    'deb_file="$(find /src/cli -maxdepth 1 -type f -name '\''allta-vm*.deb'\'' ! -name '\''*dbgsym*'\'' | sort | tail -n 1)"' \
    '' \
    'if [ -z "${deb_file}" ]; then' \
    '    echo "Не найден собранный .deb файл" >&2' \
    '    exit 1' \
    'fi' \
    '' \
    'cp "${deb_file}" /out/' \
    'echo "Собран пакет: ${deb_file}"' \
    > /usr/local/bin/build-allta-vm-cli

RUN chmod +x /usr/local/bin/build-allta-vm-cli

CMD ["/usr/local/bin/build-allta-vm-cli"]
