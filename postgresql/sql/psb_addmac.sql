\connect mtest postgres

ALTER TABLE "pgbench_accounts" SET WITH MACS;
GRANT ALL ON "pgbench_accounts" TO PUBLIC;

-- Устанавливаем метку таблицы "pgbench_accounts"
MAC LABEL ON TABLE "pgbench_accounts" IS '{2,3}';
-- Сбрасываем признак MAC CСR у таблицы "pgbench_accounts"
MAC CCR ON TABLE "pgbench_accounts" IS OFF;

-- Навешиваем метку на все поля таблицы
UPDATE "pgbench_accounts" SET maclabel="{1,1}";

