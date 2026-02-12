FROM httpd:2.4

COPY auth_init.sh /usr/local/bin/init-auth.sh
RUN chmod +x /usr/local/bin/init-auth.sh

ENTRYPOINT ["/usr/local/bin/init-auth.sh"]
