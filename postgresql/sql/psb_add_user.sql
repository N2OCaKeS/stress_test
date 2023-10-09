-- Создаем пользователей
CREATE USER u_1_01; -- метка {0,0}  00

-- Даем привилегии PARSEC_CAP_CHMAC и PARSEC_CAP_SETMAC
-- ALTER USER u_0_00 WITH PARSEC_CAP_CHMAC, PARSEC_CAP_SETMAC;



-- Создаем тестовую базу данных
CREATE DATABASE test_parsec;