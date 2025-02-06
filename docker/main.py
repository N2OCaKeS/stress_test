import core.funcsnargs as fcs
import core.variables as var
import time


try:
    if fcs.args.TEST_TYPE == "load-test":
        fcs.create_load_docker_compose(var.compose_content)
        fcs.run_command("docker-compose up -d")
        time.sleep(fcs.args.TIMER * 60)
    if fcs.args.TEST_TYPE == "http-test":
        fcs.create_http_docker_compose(var.compose_content)
        fcs.run_command("docker-compose up -d")
        time.sleep(fcs.args.TIMER * 60)
    if fcs.args.TEST_TYPE == "remove":
        fcs.run_command("docker stop $(docker ps -q)")
        fcs.run_command("docker container prune -f")

except KeyboardInterrupt:
    fcs.run_command("docker-compose down")
    print("Контейнеры остановлены и удалены.")

finally:
    fcs.run_command("docker-compose down")
    print("Контейнеры остановлены и удалены.")


