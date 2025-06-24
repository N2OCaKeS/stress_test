/****************************************
КОНТРОЛЬНЫЙ ПРИМЕР 3

Мандатное разграничение доступа включено.

Включена политика разграничения доступа к записям таблицы.

При создании записи с помощью триггера выполняется следующее:
- создаются роль с именем, равным уникальному идентификатору (id) записи;
- пользователю, создавшему запись даются права этой созданной роли.

Пользователи, имеющие права роли Администратор_БД имеют доступ ко всем записям.
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



-- УДАЛЕНИЕ И СОЗДАНИЕ РОЛЕЙ ДОСТУПА
DROP ROLE IF EXISTS Отдел_11;
DROP ROLE IF EXISTS Отдел_12;
DROP ROLE IF EXISTS Департамент_1;
DROP ROLE IF EXISTS Отдел_21;
DROP ROLE IF EXISTS Отдел_22;
DROP ROLE IF EXISTS Департамент_2;
DROP ROLE IF EXISTS Организация;
DROP ROLE IF EXISTS Пользователь;
DROP ROLE IF EXISTS Администратор_БД;
CREATE ROLE Администратор_БД;
-- Пользователя user3 назначаем администратором БД
GRANT Администратор_БД TO user3;


-- Разрешение доступа к таблице всем
GRANT ALL PRIVILEGES ON s1.t1 TO PUBLIC;
-- ВКЛЮЧЕНИЕ ПОЛИТИКИ ЗАЩИТЫ НА УРОВНЕ ЗАПИСЕЙ ДЛЯ ТАБЛИЦЫ
ALTER TABLE s1.t1 ENABLE ROW LEVEL SECURITY;
-- Разрешение всем создавать записи
CREATE POLICY org_policy2 ON s1.t1 FOR INSERT TO PUBLIC
    WITH CHECK (true);
-- Разрешение доступа к записи пользователям,
-- имеющим права роли, совпадающей с id записи
-- или имеющим права роли Администратор_БД
CREATE POLICY org_policy1 ON s1.t1 TO PUBLIC
    USING (pg_has_role(id::name, 'MEMBER')
        OR pg_has_role('Администратор_БД'::name, 'MEMBER'));

-- Создание триггерной функции, исполняемой после вставки записи
-- Функция должна принадлежать пользоватею postgres для корректной работы параметра SECURITY DEFINER
CREATE OR REPLACE FUNCTION s1.t1_trigger_after_insert()
    RETURNS trigger AS
$$
BEGIN
    -- Создаем роль доступа с именем равным id записи
    EXECUTE (format('CREATE ROLE "%s" with NOINHERIT', NEW.id));
    -- Даем права этой роли пользователю, создавшему запись
    EXECUTE (format('GRANT "%s" TO ', NEW.id) || current_user);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Создание триггерной функции, исполняемой после удаления записи
-- Функция должна принадлежать пользоватею postgres для корректной работы параметра SECURITY DEFINER
CREATE OR REPLACE FUNCTION s1.t1_trigger_after_delete()
    RETURNS trigger AS
$$
BEGIN
    -- Удаляем роль доступа с именем равным id записи
    EXECUTE (format('DROP ROLE IF EXISTS "%s"', OLD.id));
    RETURN OLD;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Создание триггера, исполняемого после вставки записи
CREATE TRIGGER tr_org_after_insert
    AFTER INSERT ON s1.t1
    FOR EACH ROW
    EXECUTE PROCEDURE s1.t1_trigger_after_insert();

-- Создание триггера, исполняемого после удаления записи
CREATE TRIGGER tr_org_after_delete
    AFTER DELETE ON s1.t1
    FOR EACH ROW
    EXECUTE PROCEDURE s1.t1_trigger_after_delete();


