FROM jordi/ab:latest

RUN apk update && \
    apk add --no-cache apache2-utils

WORKDIR /app

COPY ./load_http.sh  .

RUN chmod +x load_http.sh

CMD ["sh", "/app/load_test.sh"]