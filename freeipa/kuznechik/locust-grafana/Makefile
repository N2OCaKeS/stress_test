IMAGE_NAME = astra/grafana

build:
	docker build --progress plain . -t $(IMAGE_NAME)

shell:
	docker exec -it -u root $(IMAGE_NAME) bash

# docker run -d -P -p 3000:3000 -it --entrypoint=/bin/bash astra/grafana
