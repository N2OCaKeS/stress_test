# Модуль allta

## Описание

Модуль содержит в себе реализацию классов упрощающих написание тестов для ОС Astra Linux

## Установка

```bash
pip install -i http://10.177.103.10:3141/root/release --trust 10.177.103.10 allta
```

## Документация

Документация доступна по следующей ссылке: <http://10.177.103.10:3141/root/release/allta/latest/+d/index.html>

## Сборка и публикация библиотеки

### Автоматически

1. В релизный репозиторий

    ```bash
    git check-out libs
    git merge --no-ff dev_libs --commit -m "allta_lib v1.2.3" # В версии указать актуальную версию библиотеки
    sleep 90
    echo "Библиотека успешно опубликована"
    ```

### Вручную

1. В релизный репозиторий

    ```bash
    cd libs/allta
    pip install -y -r req.txt
    devpi use http://10.177.103.10:3141/root/release
    devpi login <username> --password=<password>
    devpi upload --with-docs
    rm -rf allta.egg-info/ build/ dist/
    ```

2. В репозиторий разработки

    ```bash
    cd libs/allta
    pip install -y -r req.txt
    devpi use http://10.177.103.10:3141/user/dev
    devpi login <username> --password=<password>
    devpi upload --with-docs
    rm -rf allta.egg-info/ build/ dist/
    ```

3. В репозиторий отладки

    ```bash
    cd libs/allta
    pip install -y -r req.txt
    devpi use http://10.177.103.10:3141/debug/debug
    devpi login <username> --password=<password>
    devpi upload --with-docs
    rm -rf allta.egg-info/ build/ dist/
    ```
