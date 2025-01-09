LOCUST_WORKERS := 1
LOCUST_PROCESSES := 1

build:
	cd locust-timescale && make build
	cd locust-grafana && make build
	cd locust-prometheus && make build
	docker build --progress plain . -t astra/locust:master

down:
	docker-compose down -v

# Run "make up LOCUST_PROCESSES=4 LOCUST_WORKERS=2" to run 2 workers with 4 processes each
# If not set it will run 1 worker with 1 process
up:
	docker-compose up --scale worker=$(LOCUST_WORKERS)
