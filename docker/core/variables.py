compose_content = f"""
version: "3.1"
services:

"""

http_content = f"""  
  nginx:
    image: nginx
    container_name: nginx
    stdin_open: true
    tty: true
    ports:
      - "80:80"
    networks:
      - load-network
  ab:
    image: ab
    container_name: ab
    stdin_open: true
    tty: true
    networks:
      - load-network

networks:
  load-network:
    driver: bridge
"""














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