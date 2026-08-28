FROM python:3.12-slim

WORKDIR /git

# devpi и инструменты сборки документации.
# Тема furo — современная, совместима с актуальным Sphinx (itcase-sphinx-theme
# ломался на Sphinx >= 7.2). conf.py пакета использует furo.
RUN pip install --no-cache-dir devpi-server devpi-web devpi-client sphinx furo

# git нужен для клонирования репозитория, curl — для healthcheck/диагностики
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Порт API и веб-интерфейса devpi
EXPOSE 3141

COPY devpi_allta_auth /git/devpi_allta_auth
RUN pip install --no-cache-dir /git/devpi_allta_auth

COPY auto_check_new_version/new_release.py /git/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
