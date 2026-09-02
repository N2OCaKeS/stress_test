"""Регрессия: scrub inline-секретов на retry-path.

Тесты этого файла раньше проверяли, что worker маскирует plaintext
`password_plaintext` / `ssh_private_key_plaintext` в `tasks.payload`
прямо в `finally`, даже если SSH или callback ломались. После фикса
server_service вообще не кладёт plaintext в payload — секреты передаются
через Redis-stash (`creds_stash_key`). Сценарий «scrub отрабатывает на
inline-плэйнтекст в payload'е» стал не воспроизводим — оставлять тесты
с `@pytest.mark.skip` смысла нет, эту регрессию закрывает запрет на
plaintext-в-payload на уровне server_service.
"""
