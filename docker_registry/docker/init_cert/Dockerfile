FROM alpine:3.20

RUN apk add --no-cache openssl bash

COPY cert.sh /usr/local/bin/init-tls.sh
RUN chmod +x /usr/local/bin/init-tls.sh

ENTRYPOINT ["/usr/local/bin/init-tls.sh"]
