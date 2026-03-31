# syntax=docker/dockerfile:1.7
FROM nginx:alpine

RUN set -eux; \
    if ! command -v update-ca-certificates >/dev/null 2>&1; then \
      if command -v apk >/dev/null 2>&1; then \
        apk add --no-cache ca-certificates; \
      elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*; \
      else \
        echo "No supported package manager to install ca-certificates" >&2; exit 1; \
      fi; \
    fi; \
    command -v update-ca-certificates >/dev/null 2>&1
RUN --mount=type=secret,id=allta_api_ca,target=/run/secrets/allta_api_ca,required=true \
    install -m 0644 /run/secrets/allta_api_ca /usr/local/share/ca-certificates/allta-api.crt && \
    update-ca-certificates

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY default.conf /etc/nginx/conf.d/default.conf

EXPOSE 443

CMD ["nginx", "-g", "daemon off;"]
