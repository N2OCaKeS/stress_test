import core.funcsnargs as fcs
import core.variables as var
import core.test_http as lc
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

    elif fcs.args.TEST_TYPE == "apache":
        fcs.run_command("docker rmi httpd")
        fcs.run_command("docker build -t httpd ./dockerfiles/http/apache-httpd")
        fcs.run_command("docker run -d --name=httpd-test --net load-network --ip 172.21.0.3 -p 80:80 httpd")
        time.sleep(60)

    if fcs.args.TEST_TYPE == "remove":
        pass

except Exception as e:
    print(f"Произошла ошибка {e}")

except KeyboardInterrupt:
    print("Операция прервана пользователем")


finally:
    fcs.run_command("docker stop $(docker ps -q)")
    fcs.run_command("docker container prune -f")
    fcs.run_command("docker rmi $(docker images -q)")
    print("Контейнеры остановлены и удалены.")


