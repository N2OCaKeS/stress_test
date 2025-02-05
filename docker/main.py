import argparse
import subprocess
import time
# from libs.zefir import UploaderZC



parser = argparse.ArgumentParser()
parser.add_argument("-o", "--docker-image",
                    type=str,
                    help="Docker base image for stress testing",
                    default="alpine:latest",
                    dest="CONT_NAME")
parser.add_argument("-c", "--count",
                    type=int,
                    help="Choose count of containers",
                    default=5,
                    dest="COUNT")
parser.add_argument("-t", "--timer",
                    type=int,
                    help="timer for test(default 1 min)",
                    default=1,
                    dest="TIMER")
parser.add_argument("-l", "--load",
                    type=int,
                    help="CPU load (default 2 workers)",
                    default=2,
                    dest="LOAD")
args = parser.parse_args()


def create_docker_compose():
    compose_content = "version: '3.1'\n\nservices:\n"

    for i in range(1, args.COUNT + 1):
        service_name = f"stress_{i}"
        service_content = f"""
  {service_name}:
    build:
      context: .
      args:
        BASE_IMAGE: {args.CONT_NAME}
    container_name: {service_name}
    command: ["stress", "--cpu", "{args.LOAD}", "--timeout", "{args.TIMER * 60}"]
    stdin_open: true
    tty: true
"""
        compose_content += service_content

    with open('docker-compose.yml', 'w') as file:
        file.write(compose_content)

    print("Файл docker-compose.yml успешно создан.")


def run_command(command):
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        print(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print("Команда завершилась с ошибкой:")
        print(e)


# try:
#     create_docker_compose()
#     run_command("docker-compose up -d")

#     time.sleep(args.TIMER * 60)

# finally:
#     run_command("docker-compose down")
#     print("Контейнеры остановлены и удалены.")
