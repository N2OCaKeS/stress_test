import core.funcsnargs as fcs
import core.variables as var
import time


try:
    fcs.create_load_docker_compose(var.compose_content)
    fcs.run_command("docker-compose up -d")
    time.sleep(fcs.args.TIMER * 60)

finally:
    fcs.run_command("docker-compose down")
    print("Контейнеры остановлены и удалены.")

