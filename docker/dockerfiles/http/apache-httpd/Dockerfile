FROM httpd:latest

COPY ./html/ /usr/local/apache2/htdocs/
COPY ./httpd.conf /usr/local/apache2/conf/httpd.conf

EXPOSE 80

RUN chown -R www-data:www-data /usr/local/apache2/htdocs/
RUN chmod -R 755 /usr/local/apache2/logs
RUN apt-get update
RUN apt install systemctl apache2
RUN systemctl start apache2
CMD ["httpd-foreground"]