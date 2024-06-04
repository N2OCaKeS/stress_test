-- RBT-TEST - Проверка утилиты pgbench
--
\i 'support/init.sql'
CREATE USER pgbench_user;
--
-- Даем необходимые права пользователю
ALTER DATABASE mtest OWNER TO pgbench_user;
ALTER SCHEMA public OWNER TO pgbench_user;
--
-- Задаём необходимые МРД атрибуты
\! useradd pgbench_user || true
\! pdpl-user -l 0:255 -c 0:0xFFFFFFFFF pgbench_user
\! usercaps -m PARSEC_CAP_CHMAC:PARSEC_CAP_SETMAC pgbench_user
MAC LABEL ON CLUSTER IS '{255,0xFFFFFFFFF}';
MAC CCR ON CLUSTER IS OFF;
MAC LABEL ON DATABASE mtest IS '{255,0xFFFFFFFFF}';
MAC CCR ON DATABASE mtest IS OFF;
MAC LABEL ON SCHEMA public IS '{255,0xFFFFFFFFF}';
MAC CCR ON SCHEMA public IS OFF;
--
-- Тестируем pgbench с поддержкой меток.
-- Обратите внимание, что не должно возникать предупреждения "buffer refcount leak" [DEVOS-4630]
-- (что должно касаться в теории только 'random' режима меток).
--
\! pgbench -p 6000 -U postgres --macs=random --initialize mtest 2>&1 | grep -vw "s"
\! pgbench -p 6000 -U pgbench_user -t 500 -j 30 -c 30 --macs=random --random-seed=13 mtest | grep -Evw "tps|ms" | grep -v "astra.se"
\! pgbench -p 6000 -U pgbench_user -t 500 -j 30 -c 30 --macs=random --macs-no-check --random-seed=13 mtest | grep -Evw "tps|ms" | grep -v "astra.se"
\! pgbench -p 6000 -U postgres --macs=fixed --initialize mtest 2>&1 | grep -vw "s"
\! pgbench -p 6000 -U pgbench_user -t 500 -j 30 -c 30 --macs=fixed --random-seed=13 mtest | grep -Evw "tps|ms" | grep -v "astra.se"
\! pgbench -p 6000 -U postgres --initialize mtest 2>&1 | grep -vw "s"
\! pgbench -p 6000 -U postgres -t 5 -c 2 --macs=disabled --random-seed=13 mtest | grep -Evw "tps|ms" | grep -v "astra.se"
\! pgbench -p 6000 -U postgres -t 5 -c 2 --random-seed=13 mtest | grep -Evw "tps|ms" | grep -v "astra.se"
--
-- Проверка различных протоколов с разными скриптами с флагом '--macs=random'
--
\! pgbench -p 6000 -U postgres --macs=random --initialize mtest 2>&1 | grep -vw "s"
-- Simple протокол
\! pgbench -p 6000 -U pgbench_user -t 5 -c 2 --macs=random -M simple -b macs-t mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 20 -c 5 --macs=random -M simple -b macs-si mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 100 -c 7 --macs=random -M simple -b macs-se mtest | grep -E "actually|maclabel"
-- Extended протокол
\! pgbench -p 6000 -U pgbench_user -t 5 -c 2 --macs=random -M extended -b macs-t mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 20 -c 5 --macs=random -M extended -b macs-si mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 100 -c 7 --macs=random -M extended -b macs-se mtest | grep -E "actually|maclabel"
-- Prepared протокол
\! pgbench -p 6000 -U pgbench_user -t 5 -c 2 --macs=random -M prepared -b macs-t mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 20 -c 5 --macs=random -M prepared -b macs-si mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 100 -c 7 --macs=random -M prepared -b macs-se mtest | grep -E "actually|maclabel"
--
-- Проверка различных протоколов с разными скриптами с флагом '--macs=fixed'
--
\! pgbench -p 6000 -U postgres --macs=fixed --initialize mtest 2>&1 | grep -vw "s"
-- Simple протокол
\! pgbench -p 6000 -U pgbench_user -t 5 -c 2 --macs=fixed -M simple -b macs-t mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 20 -c 5 --macs=fixed -M simple -b macs-si mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 100 -c 7 --macs=fixed -M simple -b macs-se mtest | grep -E "actually|maclabel"
-- Extended протокол
\! pgbench -p 6000 -U pgbench_user -t 5 -c 2 --macs=fixed -M extended -b macs-t mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 20 -c 5 --macs=fixed -M extended -b macs-si mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 100 -c 7 --macs=fixed -M extended -b macs-se mtest | grep -E "actually|maclabel"
-- Prepared протокол
\! pgbench -p 6000 -U pgbench_user -t 5 -c 2 --macs=fixed -M prepared -b macs-t mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 20 -c 5 --macs=fixed -M prepared -b macs-si mtest | grep -E "actually|maclabel"
\! pgbench -p 6000 -U pgbench_user -t 100 -c 7 --macs=fixed -M prepared -b macs-se mtest | grep -E "actually|maclabel"
--
-- Ожидаются ошибки
--
-- '--macs-no-check' и '--initialize'
\! pgbench -p 6000 -U postgres --macs=fixed --macs-no-check --initialize mtest
-- неоднозначное имя скрипта
\! pgbench -p 6000 -U pgbench_user --macs=fixed -b m mtest
-- генерация данных на стороне сервера вместе с метками не доступна (по крайней мере на данный момент)
\! pgbench -p 6000 -U postgres --macs=random --initialize --init-steps=dtGvp mtest
\! pgbench -p 6000 -U postgres --macs=fixed --initialize --init-steps=dtGvp mtest
--
\i 'support/done.sql'
\i 'support/reset-cluster-mac-attributes.sql'
DROP USER pgbench_user;
\! userdel pgbench_user