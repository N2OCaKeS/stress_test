FROM alpine:3.20

RUN apk add --no-cache openssl

WORKDIR /init
COPY init_registry_key.sh /init/init_registry_key.sh
RUN chmod +x /init/init_registry_key.sh

ENTRYPOINT ["/init/init_registry_key.sh"]
