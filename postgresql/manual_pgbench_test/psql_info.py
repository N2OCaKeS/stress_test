from libinfo import system, PostgresqlInformation

pg_rows = [("Версия:", PostgresqlInformation.get_postgresql_version() or "—")]
pg_config = PostgresqlInformation.get_postgresql_config()
pg_rows += [(f"{key}:", value or "—") for key, value in pg_config.items()]
system.print_info_frame("ИНФОРМАЦИЯ О POSTGRESQL", pg_rows)
print()