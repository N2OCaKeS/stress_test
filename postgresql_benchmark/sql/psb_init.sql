-- Создаем пользователей
CREATE USER u_0_00; -- метка {0,0}  00
CREATE USER u_1_01; -- метка {1,1}  01
--
-- Создаем тестовую базу данных
CREATE DATABASE mtest;
--
-- Подключаемся к тестовой базе
\connect mtest postgres
--
-- Настройка мандатных атрибутов для тестирования
--
-- Устанавливаем мандатную метку кластера
MAC LABEL ON CLUSTER IS '{3,3}';
--
-- Сбрасываем признак MAC CCR кластера
MAC CCR ON CLUSTER IS OFF;
--
-- Устанавливаем метку базы данных
MAC LABEL ON DATABASE mtest IS '{2,3}';
--
-- Сбрасываем признак MAC CСR у базы данных
MAC CCR ON DATABASE mtest IS OFF;
--
-- Устанавливаем метку схемы public
MAC LABEL ON SCHEMA public IS '{2,3}';
--
-- Сбрасываем признак MAC CСR у схемы public
MAC CCR ON SCHEMA public IS OFF;
--
-- Устанавливаем метку табличного пространства pg_default
MAC LABEL ON TABLESPACE pg_default IS '{2,3}';
--
-- Сбрасываем признак MAC CСR у табличного пространства pg_default
MAC CCR ON TABLESPACE pg_default IS OFF;
--
--
-- Создание тестовой таблицы
--
-- Создаем проверочную таблицу с защищенными строками
CREATE TABLE "Проверка" ("идентификатор" INTEGER PRIMARY KEY, "данные" TEXT) WITH (MACS=TRUE);
GRANT ALL ON "Проверка" TO PUBLIC;
--
-- Устанавливаем метку таблицы "Проверка"
MAC LABEL ON TABLE "Проверка" IS '{2,3}';
--
-- Сбрасываем признак MAC CСR у таблицы "Проверка"
MAC CCR ON TABLE "Проверка" IS OFF;
--
INSERT INTO "Проверка" (maclabel, "идентификатор", "данные") VALUES ("{0,0}", 1, 'initial_data');
INSERT INTO "Проверка" (maclabel, "идентификатор", "данные") VALUES ("{1,0}", 2, 'initial_data');
INSERT INTO "Проверка" (maclabel, "идентификатор", "данные") VALUES ("{1,1}", 3, 'initial_data');
