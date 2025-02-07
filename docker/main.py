import core.funcsnargs as fcs
import core.variables as var
import time
import os


try:
    if not os.path.isdir('venv'):
        fcs.run_command("bash venv_prepare.sh")
        time.sleep(15)

    if fcs.args.TEST_TYPE == "load-test":
        fcs.create_load_docker_compose(var.compose_content)
        fcs.run_command("docker-compose up -d")
        time.sleep(fcs.args.TIMER * 60)

    if fcs.args.TEST_TYPE == "http-test":
        fcs.create_http_docker_compose(var.compose_content)
        fcs.run_command("docker-compose up -d")
        time.sleep(5)
        fcs.run_command("ab -n 10000 -c 10 -g ~/git/stress_test/docker/out.data http://172.21.0.2/")
        time.sleep(fcs.args.TIMER * 60)

    if fcs.args.TEST_TYPE == "remove":
        fcs.run_command("docker stop $(docker ps -q)")
        fcs.run_command("docker container prune -f")


except KeyboardInterrupt:
    print("Операция прервана пользователем")


finally:
    fcs.run_command("docker-compose down")
    fcs.run_command("docker stop $(docker ps -q)")
    fcs.run_command("docker container prune -f")
    print("Контейнеры остановлены и удалены.")


