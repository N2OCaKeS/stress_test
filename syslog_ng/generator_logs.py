import time
import logging
import logging.handlers

from conf import QTY_HOURS_CHECK

def generate_log(duration_hours=QTY_HOURS_CHECK):
    # Создание логгера
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Настройка обработчика для syslog
    syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
    # formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    # syslog_handler.setFormatter(formatter)

    # Добавление обработчика к логгеру
    logger.addHandler(syslog_handler)

    # Количество минут для работы
    # minutes_to_run = duration_hours * 60 + 10
    
    # minutes_to_run = 20
    # minutes_passed = 0

    # while minutes_passed < minutes_to_run:
    while True:
        # Запись лога
        logger.info("MESSAGE FOR LOG")
        # Ожидание 1 минуты (60 секунд)
        time.sleep(60)
        # minutes_passed += 1
    
    # logger.info(f"Завершение записи логов после {duration_hours} часов.")


if __name__ == "__main__":
    generate_log()