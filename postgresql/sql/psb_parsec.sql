-- Устанавливаем мандатную метку кластера
MAC LABEL ON CLUSTER IS '{255,0xFFFFFFFFFFFFFFFF}';

-- Сбрасываем признак MAC CCR кластера
MAC CCR ON CLUSTER IS OFF;

-- Устанавливаем метку базы данных
MAC LABEL ON DATABASE test_parsec IS '{255,0xFFFFFFFFFFFFFFFF}';

-- Сбрасываем признак MAC CСR у базы данных
MAC CCR ON DATABASE test_parsec IS OFF;

-- Устанавливаем метку схемы public
MAC LABEL ON SCHEMA public IS '{255,0xFFFFFFFFFFFFFFFF}';

-- Сбрасываем признак MAC CСR у схемы public
MAC CCR ON SCHEMA public IS OFF;


-- #Создаем пользователя
-- sudo useradd u_1_01 && sudo usermac -m 0:255 -c 0:0xFFFFFFFFFFFFFFFF u_1_01
-- sudo usercaps -m PARSEC_CAP_CHMAC:PARSEC_CAP_SETMAC u_1_01

-- usermod -a -G shadow postgres
-- setfacl -d -m u:postgres:r /etc/parsec/macdb
-- setfacl -R -m u:postgres:r /etc/parsec/macdb
-- setfacl -m u:postgres:rx /etc/parsec/macdb
-- setfacl -d -m u:postgres:r /etc/parsec/capdb
-- setfacl -R -m u:postgres:r /etc/parsec/capdb
-- setfacl -m u:postgres:rx /etc/parsec/capdb