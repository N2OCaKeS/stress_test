# Postgresql balance

## Запуск теста

1. Установка зависимойстей

    ```bash
    sudo ./manual_bl_prepare.sh <версия rc>
    ```

    Пример использования:

    ```bash
    sudo ./manual_bl_prepare.sh 1.8.3.3
    ```

2. Активация venv

    ```bash
    sudo su
    source python/Python-3.12.1/venv/bin/activate
    ```

3. Запуск скрипта

    ```bash
    python manual_bl_run.py -bv <версия rc> -sec <режим защищенности буква>
    ```

    Пример использования:

    ```bash
    python manual_bl_run.py -bv 1.8.1.UU.2.4 -sec s
    ```

Для получения дополнительной информации о запуске скрипта выполните:

```bash
python manual_bl_run.py --help
```
