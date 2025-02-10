import core.funcsnargs as fcs
import core.variables as var
import time


try:
    if fcs.args.TEST_TYPE == "load":
        fcs.create_load_docker_compose(var.compose_content)
        fcs.run_command("docker-compose up -d")
        fcs.collect_stats_in_thread()

        fcs.html_converter("protocols/csv_format/system_stats.csv")
        fcs.html_converter("protocols/csv_format/container_stats.csv")
        # fcs.plot_system_stats(filename="system_stats.csv")
        # fcs.plot_system_stats(filename="container_stats.csv")

    if fcs.args.TEST_TYPE == "http":
        fcs.create_http_docker_compose(var.compose_content)
        fcs.run_command("docker-compose up -d")
        time.sleep(5)
        fcs.run_command("ab -n 100 -c 10 -g ~/git/stress_test/docker/out.data http://172.21.0.2/")
        fcs.run_command("ab -n 100 -c 10 -T application/json -p data.json http://172.21.0.2/")
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


