compose_content = """
version: "3.1"

networks:
  load-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.21.0.0/16

services:

"""

nginx_content = """  
  nginx:
    image: nginx
    container_name: nginx
    stdin_open: true
    tty: true
    ports:
      - "80:80"
    environment:
      - NGINX_PORT: "80"
    networks:
      load-network:
        ipv4_address: 172.21.0.2

"""


# apache_content = """
#   apache:
#     image: apache
#     container_name: apache
#     stdin_open: true
#     tty: true
#     ports:
#       - "80:80"
#     environment:
#       HTTPD_PORT: "80"
#     networks:
#       load-network:
#         ipv4_address: 172.21.0.3

# """

  # ab:
  #   image: ab
  #   container_name: ab
  #   stdin_open: true
  #   tty: true
  #   networks:
  #     load-network:
  #       ipv4_address: 172.21.0.3













  # base_load_service:
  #   build:
  #     context: .
  #     dockerfile: dockerfiles/Dockerfile.load
  #     args:
  #       BASE_IMAGE: {args.CONT_NAME}
  #   image: stress_test_image
  # http_attacker:
  #   build:
  #     context: .
  #     dockerfile: dockerfiles/Dockerfile.ab
  #     args:
  #       BASE_IMAGE: jordi/ab:latest
  #   image: ab_image

  # http_server:
  #   build:
  #     context: .
  #     dockerfile: dockerfiles/Dockerfile.nginx
  #     args:
  #       BASE_IMAGE: nginx:latest
  #   image: nginx_image
  #   ports:
  #     - "80:80"