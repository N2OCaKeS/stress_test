/****************************************
КОНТРОЛЬНЫЙ ПРИМЕР 0

Мандатное разграничение доступа включено.

Дискреционное (ролевое) разграничение доступа не применяется.
*****************************************/
-- ЧИСТКА ТАБЛИЦЫ
-- Подключение к БД контрольного примера
\connect contrprimer
-- Удаление строк для выполнения триггеров
DELETE FROM s1.t1;

-- СОЗДАНИЕ НОВОЙ БАЗЫ ДАННЫХ
-- Подключение к системной БД
\connect postgres

-- Задание правил мандатного доступа доступа к кластеру
MAC CCR ON CLUSTER IS OFF;
MAC LABEL ON CLUSTER IS '{3,0}';

-- Удаление БД контрольного примера
DROP DATABASE IF EXISTS contrprimer;
-- Создание пустой БД контрольного примера
CREATE DATABASE contrprimer;

-- СОЗДАНИЕ ОБЪЕКТОВ БАЗЫ ДАННЫХ
-- Подключение к БД контрольного примера
\connect contrprimer

-- Задание правил мандатного доступа доступа к базе занных
MAC CCR ON DATABASE contrprimer IS OFF;
MAC LABEL ON DATABASE contrprimer IS '{3,0}';

-- Создание схемы
CREATE SCHEMA s1;

-- Задание правил мандатного доступа доступа к схеме
MAC CCR ON SCHEMA s1 IS OFF;
MAC LABEL ON SCHEMA s1 IS '{3,0}';

-- Задание правил дискреционного доступа к схеме
GRANT USAGE ON SCHEMA s1 TO PUBLIC;
-- Задание расширения для работы с уникальными идентификаторами
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA s1;
-- Создание таблицы
CREATE TABLE s1.t1 (
  id uuid DEFAULT s1.uuid_generate_v4() NOT NULL,
  insert_user name,
  insert_date timestamp,
  update_user name,
  update_date timestamp,
  classificator name
) WITH (
  MACS=TRUE -- Создание классификационных меток для записей
);
-- Задание правил мандатного доступа доступа к таблице
MAC CCR ON TABLE s1.t1 IS OFF;
MAC LABEL ON TABLE s1.t1 IS '{3,0}';


-- Дискреционный доступ разрешен всем
GRANT ALL PRIVILEGES ON s1.t1 TO PUBLIC;
