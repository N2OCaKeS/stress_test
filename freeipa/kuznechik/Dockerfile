# docker build . -t astra/locust:master
# docker run -d -P -it --rm --name master-locust -v $(pwd):/home/locust astra/locust:master --class-picker
FROM locustio/locust:master

# Allta devpi package index
RUN pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release && \
    pip config --global set global.trusted-host allta.devos.astralinux.ru

USER root
RUN apt-get update && \
    apt-get install -y libsasl2-dev libldap2-dev libkrb5-dev gcc python3-dev libsasl2-modules-gssapi-mit krb5-config krb5-user && \
    pip3 install bonsai psycopg2-binary psycogreen locust_plugins gssapi
COPY ["krb5.conf", "/etc/krb5.conf"]
USER locust
