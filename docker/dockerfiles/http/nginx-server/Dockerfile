#dockerfile.nginx
FROM nginx:latest


WORKDIR /usr/share/nginx/html
COPY ./static/index.html .

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]