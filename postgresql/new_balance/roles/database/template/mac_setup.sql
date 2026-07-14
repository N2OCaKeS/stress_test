-- Настройка MAC-меток на таблицах базы protopack и создание сервисного пользователя.
-- Выполняется методом new_balance/roles/database/db.py::setup_mac().
--
-- Запускается на сервере БД (database1) от имени postgres после того,
-- как new_balance/roles/database/db.py::setup_protopack() создал базу и наполнил её данными.
--
-- Уровни МРД (по имени build: testing / unstable-sid — единственный различающий признак
-- в реальных дампах, т.к. rel всегда NULL):
--   {0,0} — открытые данные, все релизы кроме testing/unstable/sid (видно всем)
--   {1,0} — debian.forky.testing (допуск >= 1:0)
--   {2,0} — debian.sid.unstable, самая нестабильная ветка (допуск >= 2:0)
-- Категория (2-е число) везде 0 — компартменты не используются, важен только уровень.
-- Контейнеры (база/схема/таблицы) размечены тем же потолком {2,0}, чтобы вместить
-- максимальный уровень строк.
--
-- Важно: реальные строки после импорта дампов (build_info, build_packages_new,
-- build_sourses) лежат не в main.*, а в other.build_info/build_packages/build_sources —
-- каждый дамп создаёт свою таблицу через `CREATE TABLE other.xxx (...) INHERITS (main.xxx)
-- WITH (MACS=FALSE)`. main.* при этом остаются пустыми (0 строк) и служат только
-- родителем для наследования и точкой SELECT в app.py (`FROM main.build_info` без ONLY
-- захватывает и строки other.* через INHERITS). Строк там нет и CHMAC им не нужен, но
-- метку {0,0} на схему/таблицы main всё равно проставляем явно: они тоже дочерние
-- объекты базы protopack, и если оставить им дефолтную/унаследованную метку с не
-- нулевой категорией, это заблокирует смену категории у самой базы (именно так
-- случилось на живой БД, где main.* остались от более раннего ручного теста с {1,1}).

-- Разрешаем postgres читать метки
-- (выполняется заранее в new_balance/roles/database/db.py::settings(),
-- шаг 'set postgres privilege', через pdpl-user и setfacl)

-- Метка кластера (pg_global) — вершина иерархии контейнеров, должна быть
-- не ниже метки любой БД в кластере (сейчас максимум {2,0} у protopack).
-- Категория везде 0 — компартменты (2-е число) этому тесту не нужны, важны
-- только уровни. {3,0} даёт запас под более высокий уровень в будущем,
-- совпадает с диапазоном pdpl-user postgres (0:3).
MAC LABEL ON CLUSTER IS '{3,0}';
MAC CCR    ON CLUSTER IS OFF;

-- Метка базы
MAC LABEL ON DATABASE protopack IS '{2,0}';
MAC CCR    ON DATABASE protopack IS OFF;

-- Метка схемы (дамп схему other не размечает)
MAC LABEL ON SCHEMA other IS '{2,0}';
MAC CCR    ON SCHEMA other IS OFF;

-- main.* пустые (см. примечание выше), метка минимальная — {0,0}
MAC LABEL ON SCHEMA main IS '{0,0}';
MAC CCR    ON SCHEMA main IS OFF;
MAC LABEL ON TABLE main.build_info     IS '{0,0}';
MAC CCR    ON TABLE main.build_info     IS OFF;
MAC LABEL ON TABLE main.build_packages IS '{0,0}';
MAC CCR    ON TABLE main.build_packages IS OFF;
MAC LABEL ON TABLE main.build_sources  IS '{0,0}';
MAC CCR    ON TABLE main.build_sources  IS OFF;

-- Метки таблиц — переопределяем то, что проставил дамп (там {0,0} / CCR ON,
-- то есть контейнер полностью открыт и раньше клэмпил все строки к {0,0}
-- независимо от per-row maclabel). CCR OFF нужен, чтобы дальше решала
-- именно метка конкретной строки, а не метка таблицы.
-- Важно: это должно идти ДО "ALTER TABLE ... SET WITH MACS" — существующие
-- строки при включении MACS наследуют текущую метку ТАБЛИЦЫ на момент этой
-- команды, а не ту, что будет установлена позже. Если сделать наоборот, все
-- строки останутся на {0,0} от дампа, как и произошло в предыдущем прогоне.
MAC LABEL ON TABLE other.build_info     IS '{2,0}';
MAC CCR    ON TABLE other.build_info     IS OFF;
MAC LABEL ON TABLE other.build_packages IS '{2,0}';
MAC CCR    ON TABLE other.build_packages IS OFF;
MAC LABEL ON TABLE other.build_sources  IS '{2,0}';
MAC CCR    ON TABLE other.build_sources  IS OFF;

-- Включаем мандатные метки на таблицах, где реально лежат данные.
-- Дамп создаёт эти таблицы с WITH (MACS=FALSE) — здесь явно включаем обратно.
-- Существующие строки получат метку таблицы {2,0}, выставленную строками выше.
ALTER TABLE other.build_info     SET WITH MACS;
ALTER TABLE other.build_packages SET WITH MACS;
ALTER TABLE other.build_sources  SET WITH MACS;

-- Открытые сборки (уровень 0:0 — видны всем пользователям).
-- rel всегда NULL в реальных дампах, поэтому делим по имени build:
-- testing -> {1,0}, unstable/sid -> {2,0}, остальные (номерные релизы) -> {0,0}.
-- maclabel — системный столбец, обычный UPDATE его не меняет; нужна команда CHMAC
-- (требует привилегии ac_capable_chmac / PARSEC_CAP_CHMAC у postgres).
-- ONLY обязателен: без него CHMAC пытается построить план и по потомкам через
-- INHERITS, а у тех, что без MACS, нет столбца maclabel, и планировщик падает
-- с "variable not found in subplan target list".
CHMAC ONLY other.build_info     SET maclabel='{0,0}'
    WHERE build NOT LIKE '%testing%' AND build NOT LIKE '%unstable%' AND build NOT LIKE '%sid%';
CHMAC ONLY other.build_packages SET maclabel='{0,0}'
    WHERE build IN (
        SELECT build FROM other.build_info
        WHERE build NOT LIKE '%testing%' AND build NOT LIKE '%unstable%' AND build NOT LIKE '%sid%'
    );
CHMAC ONLY other.build_sources  SET maclabel='{0,0}'
    WHERE build IN (
        SELECT build FROM other.build_info
        WHERE build NOT LIKE '%testing%' AND build NOT LIKE '%unstable%' AND build NOT LIKE '%sid%'
    );

-- debian.forky.testing — уровень 1:0 (видны пользователям с допуском >= 1:0)
CHMAC ONLY other.build_info     SET maclabel='{1,0}' WHERE build LIKE '%testing%';
CHMAC ONLY other.build_packages SET maclabel='{1,0}'
    WHERE build IN (SELECT build FROM other.build_info WHERE build LIKE '%testing%');
CHMAC ONLY other.build_sources  SET maclabel='{1,0}'
    WHERE build IN (SELECT build FROM other.build_info WHERE build LIKE '%testing%');

-- debian.sid.unstable — уровень 2:0, самая нестабильная ветка (допуск >= 2:0)
CHMAC ONLY other.build_info     SET maclabel='{2,0}'
    WHERE build LIKE '%unstable%' OR build LIKE '%sid%';
CHMAC ONLY other.build_packages SET maclabel='{2,0}'
    WHERE build IN (SELECT build FROM other.build_info WHERE build LIKE '%unstable%' OR build LIKE '%sid%');
CHMAC ONLY other.build_sources  SET maclabel='{2,0}'
    WHERE build IN (SELECT build FROM other.build_info WHERE build LIKE '%unstable%' OR build LIKE '%sid%');

-- Сервисный пользователь для веб-приложения
-- Подключается от имени protopack_web; реальная фильтрация — по MAC-метке сокета
CREATE USER protopack_web;
GRANT CONNECT ON DATABASE protopack TO protopack_web;

GRANT USAGE   ON SCHEMA main TO protopack_web;
GRANT SELECT  ON ALL TABLES IN SCHEMA main TO protopack_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA main GRANT SELECT ON TABLES TO protopack_web;

-- app.py читает main.build_info без ONLY, а это разворачивается в отдельные
-- SELECT по каждой участвующей в наследовании таблице — нужны права и на other.
GRANT USAGE   ON SCHEMA other TO protopack_web;
GRANT SELECT  ON ALL TABLES IN SCHEMA other TO protopack_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA other GRANT SELECT ON TABLES TO protopack_web;

-- Собственной MAC-меткой роль не наделяется (фильтрация строк идёт по метке
-- сокета, которую подставляет Apache/AstraMode, а не по правам роли). Но
-- postgres требует, чтобы у роли был соответствующий ОС-пользователь с
-- диапазоном MAC (pdpl-user) в /etc/parsec/macdb, иначе подключение падает с
-- "error obtaining MAC configuration for user protopack_web" — это делается
-- на уровне ОС, в db.py::setup_mac() (Python), не здесь.
