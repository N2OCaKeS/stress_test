/****************************************
КОНТРОЛЬНЫЙ ПРИМЕР

ТАБЛИЦА 1
Мандатное разграничение доступа включено.
Дискреционное (ролевое) разграничение доступа не применяется.

ТАБЛИЦА 2
Мандатное разграничение доступа включено.
Включена политика разграничения доступа к записям таблицы.
При выполнении SELECT, UPDATE и DELETE доступ зависит от текущего времени.
Количество секунд в поле insert_date сравнивается с количеством секунд времени запроса.
Доступ предоставляется только к записям, созданным в первой или второй половине минуты в зависимости от времени запроса.

ТАБЛИЦА 3
Мандатное разграничение доступа включено.
Включена политика разграничения доступа к полям и записям таблицы.
Пользователи, имеющие права роли Администратор_БД имеют доступ на чтение
ко всем записям, а также могут создавать, удалять и классифицровать записи.
Пользователи, имеющие права роли Пользователь имеют доступ на чтение 
только к записям в соответствии с иерархической классификацией подразделений
и могут измененять поля update_user, update_date.

ТАБЛИЦА 4
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
DROP TABLE s1.t1;
DELETE FROM s1.t2;
DROP TABLE s1.t2;
DELETE FROM s1.t3;
DROP TABLE s1.t3;
DELETE FROM s1.t4;
DROP TABLE s1.t4;

-- УДАЛЕНИЕ РОЛЕЙ ДОСТУПА
DROP ROLE IF EXISTS Отдел_11;
DROP ROLE IF EXISTS Отдел_12;
DROP ROLE IF EXISTS Департамент_1;
DROP ROLE IF EXISTS Отдел_21;
DROP ROLE IF EXISTS Отдел_22;
DROP ROLE IF EXISTS Департамент_2;
DROP ROLE IF EXISTS Организация;
DROP ROLE IF EXISTS Пользователь;
DROP ROLE IF EXISTS Администратор_БД;

-- СОЗДАНИЕ РОЛЕЙ ДОСТУПА
CREATE ROLE Отдел_11;
CREATE ROLE Отдел_12;
-- Роль Департамент_1 включает права ролей Отдел_11, Отдел_12
CREATE ROLE Департамент_1 IN ROLE Отдел_11, Отдел_12;
CREATE ROLE Отдел_21;
CREATE ROLE Отдел_22;
-- Роль Департамент_2 включает права ролей Отдел_21, Отдел_22
CREATE ROLE Департамент_2 IN ROLE Отдел_21, Отдел_22;
-- Роль Организация включает права ролей Департамент_1, Департамент_2
CREATE ROLE Организация IN ROLE Департамент_1, Департамент_2;
-- Создаем роль администратора БД
CREATE ROLE Администратор_БД;
-- Пользователя user3 назначаем администратором БД
GRANT Администратор_БД TO user3;
-- Создаем роль пользоватля БД
CREATE ROLE Пользователь;
-- Пользователей user0, user1, user2 назначаем пользователями БД
GRANT Пользователь TO user0, user1, user2;
-- Пользователям user0, user1, user2 назначаем доступ к записям
-- в соответствии с иерархической структурой
GRANT Отдел_12 TO user0;
GRANT Департамент_1 TO user1;
GRANT Организация TO user2;


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


---------------------
-- Создание таблицы 1
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


---------------------
-- Создание таблицы 2
CREATE TABLE s1.t2 (
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
MAC CCR ON TABLE s1.t2 IS OFF;
MAC LABEL ON TABLE s1.t2 IS '{3,0}';


-- Дискреционный доступ разрешен всем
GRANT ALL PRIVILEGES ON s1.t2 TO PUBLIC;
-- ВКЛЮЧЕНИЕ ПОЛИТИКИ ЗАЩИТЫ НА УРОВНЕ ЗАПИСЕЙ ДЛЯ ТАБЛИЦЫ
ALTER TABLE s1.t2 ENABLE ROW LEVEL SECURITY;
-- Создание записи разрешено всегда
CREATE POLICY org_policy21 ON s1.t2 FOR INSERT TO PUBLIC
    WITH CHECK (true);
-- Чение, изменение и удаление разрешено для записей,
-- созданных в первой или второй половине минуты
-- соответственно текущему времени запроса.
CREATE POLICY org_policy22 ON s1.t2 TO PUBLIC
    USING ((date_part('second', now()) - 30) * (date_part('second', insert_date) - 30) > 0);


---------------------
-- Создание таблицы 3
CREATE TABLE s1.t3 (
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
MAC CCR ON TABLE s1.t3 IS OFF;
MAC LABEL ON TABLE s1.t3 IS '{3,0}';


-- РАЗГРАНИЧЕНИЕ ДОСТУПА К ПОЛЯМ ТАБЛИЦЫ
-- Роли Администратор_БД разрешено чтение, вставка и удаление записей
GRANT SELECT, INSERT, DELETE ON s1.t3 TO Администратор_БД;
-- Роли Администратор_БД разрешено изменение поля classificator
GRANT UPDATE (classificator) ON s1.t3 TO Администратор_БД;
-- Роли Пользователь разрешено чтение записей
GRANT SELECT ON s1.t3 TO Пользователь;
-- Роли Пользователь разрешено изменение полей update_user, update_date
GRANT UPDATE (update_user, update_date) ON s1.t3 TO Пользователь;

-- ВКЛЮЧЕНИЕ ПОЛИТИКИ ЗАЩИТЫ НА УРОВНЕ ЗАПИСЕЙ ДЛЯ ТАБЛИЦЫ
ALTER TABLE s1.t3 ENABLE ROW LEVEL SECURITY;
-- Роли Администратор_БД разрешен доступ ко всем записям
CREATE POLICY org_policy_31 ON s1.t3 FOR ALL TO PUBLIC
    USING (pg_has_role('Администратор_БД', 'MEMBER'))
    WITH CHECK (pg_has_role('Администратор_БД', 'MEMBER'));
-- Роли Пользователь разрешен доступ в соответствии со значением поля classificator
CREATE POLICY org_policy_32 ON s1.t3 FOR ALL TO PUBLIC
    USING (pg_has_role(classificator::name, 'MEMBER'))
    WITH CHECK (pg_has_role(classificator::name, 'MEMBER'));

    
---------------------
-- Создание таблицы 4
CREATE TABLE s1.t4 (
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
MAC CCR ON TABLE s1.t4 IS OFF;
MAC LABEL ON TABLE s1.t4 IS '{3,0}';


-- Разрешение доступа к таблице всем
GRANT ALL PRIVILEGES ON s1.t4 TO PUBLIC;
-- ВКЛЮЧЕНИЕ ПОЛИТИКИ ЗАЩИТЫ НА УРОВНЕ ЗАПИСЕЙ ДЛЯ ТАБЛИЦЫ
ALTER TABLE s1.t4 ENABLE ROW LEVEL SECURITY;
-- Разрешение всем создавать записи
CREATE POLICY org_policy41 ON s1.t4 FOR INSERT TO PUBLIC
    WITH CHECK (true);
-- Разрешение доступа к записи пользователям,
-- имеющим права роли, совпадающей с id записи
-- или имеющим права роли Администратор_БД
CREATE POLICY org_policy42 ON s1.t4 TO PUBLIC
    USING (pg_has_role(id::name, 'MEMBER')
        OR pg_has_role('Администратор_БД'::name, 'MEMBER'));

-- Создание триггерной функции, исполняемой после вставки записи
CREATE OR REPLACE FUNCTION s1.t4_trigger_after_insert()
    RETURNS trigger AS
$$
BEGIN
    -- Создаем роль доступа с именем равным id записи
    EXECUTE (format('CREATE ROLE "%s" with NOINHERIT', NEW.id));
    -- Даем права этой роли пользователю, создавшему запись
    EXECUTE (format('GRANT "%s" TO ', NEW.id) || current_user);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Создание триггерной функции, исполняемой после удаления записи
CREATE OR REPLACE FUNCTION s1.t4_trigger_after_delete()
    RETURNS trigger AS
$$
BEGIN
    -- Удаляем роль доступа с именем равным id записи
    EXECUTE (format('DROP ROLE IF EXISTS "%s"', OLD.id));
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

-- Создание триггера, исполняемого после вставки записи
CREATE TRIGGER tr_org_after_insert
    AFTER INSERT ON s1.t4
    FOR EACH ROW
    EXECUTE PROCEDURE s1.t4_trigger_after_insert();

-- Создание триггера, исполняемого после удаления записи
CREATE TRIGGER tr_org_after_delete
    AFTER DELETE ON s1.t4
    FOR EACH ROW
    EXECUTE PROCEDURE s1.t4_trigger_after_delete();
