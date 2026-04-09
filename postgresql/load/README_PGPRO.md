

# Инструкция:
Конфигурация PostgreSQL (postgresql.conf)
Включить сбор статистики и логирование:

Включить сбор статистики, так как он генерирует системные вызовы:
```bash
shared_preload_libraries = 'pg_stat_statements'
```

Логирование также добавляет нагрузку на файловую систему и аудит
```bash
log_connections = on
log_disconnections = on
log_lock_waits = on
```

## Подготовка данных:
Необходимо создать отношение
```bash
psql -U postgres -d test -p 6000
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT * FROM pg_stat_statements LIMIT 100;
```

Необходимо создать множество таблиц для наполнения кэша каталога
```bash
psql -p 6000 -U postgres -d test -c "DO \$\$ BEGIN FOR i IN 1..1000 LOOP EXECUTE 'CREATE TABLE IF NOT EXISTS t_many_' || i || ' (id int)'; END LOOP; END \$\$;"
```
Примечание: В командах используется порт 6000, так как на стенде PostgreSQL сконфигурирован для работы на нестандартном порту.

### Скрипт нагрузки (/tmp/stat.sql)
Эмуляция массовой проверки атрибутов файлов
```bash
DO $$ 
BEGIN 
  FOR i IN 1..100 LOOP 
    PERFORM pg_relation_size(oid) FROM pg_class WHERE relkind = 'r' LIMIT 100; 
  END LOOP; 
END $$;
```

Запуск теста (pgbench из под пользователя postgres)
-c 20: 20 клиентов
-j 20: 20 потоков
-T 60: 1 минута теста
-C: Reconnect mode 
```bash
sudo perf record -g -a /opt/pgpro/ent-17/bin/pgbench -p 5432 -U postgres test -c 200 -j 200 -T 30 -P 5 -C -f /home/tester/devos-8712/stat.sql
```

Результаты perf top
Утилита perf top получает:
native_queued_spin_lock_slowpath: ~58.65% (Система простаивает в ожидании блокировок).
_raw_spin_lock: ~6.42%.
i_pdpl_get / parsec_inode_permission: ~2-4% (Функции Parsec).