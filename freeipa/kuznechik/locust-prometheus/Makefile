IMAGE_NAME = astra/prometheus

build:
	docker build --progress plain . -t $(IMAGE_NAME)

shell:
	docker exec -it -u root --entrypoint=/bin/bash $(IMAGE_NAME) bash
