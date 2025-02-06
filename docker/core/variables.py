from core.funcsnargs import args

compose_content = f"""
version: 3.1
services:
  base_load_service:
    build:
      context: .
      dockerfile: dockerfiles/Dockerfile.load
      args:
        BASE_IMAGE: {args.CONT_NAME}
    image: stress_test_image

  http_attacker:
    build:
      context: .
      dockerfile: dockerfiles/Dockerfile.ab
      args:
        BASE_IMAGE: jordi/ab:latest
    image: ab_image

  http_server:
    build:
      context: .
      dockerfile: dockerfiles/Dockerfile.nginx
      args:
        BASE_IMAGE: nginx:latest
    image: nginx_image
    ports:
      - "80:80"
"""