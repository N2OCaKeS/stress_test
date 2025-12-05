-- Устанавливаем мандатную метку кластера
MAC LABEL ON CLUSTER IS '{255,0xFFFFFFFFF}';

-- Сбрасываем признак MAC CCR кластера
MAC CCR ON CLUSTER IS OFF;

-- Устанавливаем метку базы данных
MAC LABEL ON DATABASE test_parsec IS '{255,0xFFFFFFFFF}';

-- Сбрасываем признак MAC CСR у базы данных
MAC CCR ON DATABASE test_parsec IS OFF;

-- Устанавливаем метку схемы public
MAC LABEL ON SCHEMA public IS '{255,0xFFFFFFFFF}';

-- Сбрасываем признак MAC CСR у схемы public
MAC CCR ON SCHEMA public IS OFF;


