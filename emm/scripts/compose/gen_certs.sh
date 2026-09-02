#!/usr/bin/env bash
# Генерация внутреннего CA + leaf-сертификата для reverse-proxy (make prod-up).
#
# Самоподписанный CA «EMM Internal CA» (O=Astra Linux, OU=DBOS) — тот же
# субъект, что у prod-CA в k8s (k8s/91-ca-issuer.yaml). Leaf выписывается этим
# CA под SAN = PROD_HOST/PROD_IP (дефолт localhost + 127.0.0.1).
#
# Кладёт в CERTS_DIR (дефолт ./certs, bind-mount в reverse-proxy):
#   emm-ca.crt  — публичный корневой сертификат CA (отдаётся клиентам на импорт)
#   emm-ca.key  — приватный ключ CA (0600, наружу не выходит)
#   tls.crt     — fullchain: leaf + CA (ssl_certificate nginx'а)
#   tls.key     — приватный ключ leaf'а (ssl_certificate_key)
#
# Идемпотентность: если tls.crt и emm-ca.crt уже есть — НЕ перегенерируем
# (иначе оператору пришлось бы переимпортировать CA после каждого prod-up).
#
# Использование:
#   scripts/compose/gen_certs.sh                       — SAN=localhost,127.0.0.1
#   PROD_HOST=emm.example.com scripts/compose/gen_certs.sh
#   PROD_IP=10.177.103.102     scripts/compose/gen_certs.sh
#   PROD_HOST=emm.example.com PROD_IP=10.177.103.102 scripts/compose/gen_certs.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CERTS_DIR="${CERTS_DIR:-$ROOT_DIR/certs}"

CA_CRT="$CERTS_DIR/emm-ca.crt"
CA_KEY="$CERTS_DIR/emm-ca.key"
LEAF_CRT="$CERTS_DIR/tls.crt"
LEAF_KEY="$CERTS_DIR/tls.key"

if [[ -f "$LEAF_CRT" && -f "$CA_CRT" ]]; then
    echo "→ Сертификаты уже есть в $CERTS_DIR — оставляю (не переимпортировать CA)."
    exit 0
fi

mkdir -p "$CERTS_DIR"
umask 077

# SAN: hostname из PROD_HOST + IP из PROD_IP; если оба пусты — localhost/127.0.0.1.
PROD_HOST="${PROD_HOST:-}"
PROD_IP="${PROD_IP:-}"
declare -a SAN_LINES=()
if [[ -z "$PROD_HOST" && -z "$PROD_IP" ]]; then
    SAN_LINES+=("DNS.1 = localhost")
    SAN_LINES+=("IP.1 = 127.0.0.1")
    LEAF_CN="localhost"
else
    dns_i=1
    ip_i=1
    LEAF_CN=""
    if [[ -n "$PROD_HOST" ]]; then
        SAN_LINES+=("DNS.${dns_i} = ${PROD_HOST}")
        dns_i=$((dns_i + 1))
        LEAF_CN="$PROD_HOST"
    fi
    if [[ -n "$PROD_IP" ]]; then
        SAN_LINES+=("IP.${ip_i} = ${PROD_IP}")
        ip_i=$((ip_i + 1))
        [[ -z "$LEAF_CN" ]] && LEAF_CN="$PROD_IP"
    fi
    # localhost всегда добавляем — healthcheck'и и обращение с самого хоста.
    SAN_LINES+=("DNS.${dns_i} = localhost")
    SAN_LINES+=("IP.${ip_i} = 127.0.0.1")
fi

SAN_BLOCK="$(printf '%s\n' "${SAN_LINES[@]}")"

echo "→ Генерирую внутренний CA (EMM Internal CA)..."
openssl genrsa -out "$CA_KEY" 4096 2>/dev/null
openssl req -x509 -new -nodes -key "$CA_KEY" -sha256 -days 3650 \
    -subj "/O=Astra Linux/OU=DBOS/CN=EMM Internal CA" \
    -out "$CA_CRT" 2>/dev/null

echo "→ Генерирую leaf-сертификат reverse-proxy (SAN: ${LEAF_CN} + localhost)..."
LEAF_CSR="$CERTS_DIR/tls.csr"
EXT_CNF="$CERTS_DIR/leaf-ext.cnf"

cat > "$EXT_CNF" <<EOF
[req]
distinguished_name = dn
req_extensions = v3_req
prompt = no

[dn]
O = Astra Linux
OU = DBOS
CN = ${LEAF_CN}

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
${SAN_BLOCK}
EOF

openssl genrsa -out "$LEAF_KEY" 2048 2>/dev/null
openssl req -new -key "$LEAF_KEY" -out "$LEAF_CSR" -config "$EXT_CNF" 2>/dev/null
openssl x509 -req -in "$LEAF_CSR" \
    -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
    -out "$CERTS_DIR/leaf.crt" -days 825 -sha256 \
    -extfile "$EXT_CNF" -extensions v3_req 2>/dev/null

# fullchain для nginx: leaf + CA (клиент, доверяющий CA, валидирует цепочку).
cat "$CERTS_DIR/leaf.crt" "$CA_CRT" > "$LEAF_CRT"

# Промежуточные артефакты не нужны reverse-proxy.
rm -f "$LEAF_CSR" "$EXT_CNF" "$CERTS_DIR/leaf.crt" "$CERTS_DIR/.srl" "$CERTS_DIR"/*.srl 2>/dev/null || true

chmod 600 "$CA_KEY" "$LEAF_KEY"
chmod 644 "$CA_CRT" "$LEAF_CRT"

echo ""
echo "✓ Сертификаты в $CERTS_DIR:"
echo "    emm-ca.crt — импортируй в доверенные корневые (или скачай с https://<host>/emm-ca.crt)"
echo "    tls.crt / tls.key — leaf для reverse-proxy"
