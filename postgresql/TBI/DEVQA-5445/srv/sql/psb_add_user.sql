-- Создаем пользователей
CREATE USER u_1;

-- Создаем тестовую базу данных
CREATE DATABASE test_parsec;

-- Даем необходимые права пользователю 
ALTER DATABASE test_parsec OWNER TO u_1;
-- ALTER TABLESPACE pg_global OWNER TO u_1;
ALTER SCHEMA public OWNER TO u_1;
