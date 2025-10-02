# Allta auth

Централизованный сервис для авторизации в allta

## Информация

### Открытые порты

Иные порты кроме как открых закрыты и доступ к сервисам осуществляется только через внутренние сети docker. Многие контейнеры разделены на различные сети для исключения конфликтов.

| Порт  | Сервис | Для чего нужен |
| :---: | :----: | -------------- |
| 21500 | nginx  | Прокси для api |

### Сервисы входящие в состав

|    Сервис    |                   Описание                   |                                         Точка входа                                          |                                      Документация                                      |
| :----------: | :------------------------------------------: | :------------------------------------------------------------------------------------------: | :------------------------------------------------------------------------------------: |
|  ALLTA Auth  |        Единый сервис для авторизации         |   [http://allta.devos.astralinux.ru/api/auth/](http://allta.devos.astralinux.ru/api/auth/)   | [http://allta.devos.astralinux.ru/api/docs](http://allta.devos.astralinux.ru/api/docs) |
| ALLTA Config | Сервис для получения конфигурационных файлов | [http://allta.devos.astralinux.ru/api/config/](http://allta.devos.astralinux.ru/api/config/) | [http://allta.devos.astralinux.ru/api/docs](http://allta.devos.astralinux.ru/api/docs) |



## Установка

1. Запустить скрипт:

    ```bash
    mkdir folder_git_for_allta_auth # Можно использовать любое название данное используется как пример
    cd folder_git_for_allta_auth
    git clone ssh://git@git.astralinux.ru:7999/qa/stress_test.git
    git checkout allta_auth
    ```

2. Необходимо запустить скрипт установки зависимостей и генерации конфигураций

    ```bash
    sudo ./install.sh precond
    ```

3. Необходимо настроить креды, для этого надо выполниться следующее

    ```bash
    cd /var/allta_services/config
    nano env.allta_auth_db
    nano env.allta_auth_api
    nano env.allta_config_api
    ```
