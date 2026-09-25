# Публичный релиз

## Что опубликовано

Публичный репозиторий содержит актуальную реализацию PocketBook Cloud skill, синхронизированную с private production-источником:

- `SKILL.md`;
- CLI `scripts/pocketbook.py`;
- модульный `scripts/pocketbook_lib/`;
- API/troubleshooting references;
- synthetic contract fixtures;
- unit, contract и hardening tests;
- RU/EN документацию;
- CI и `tools/release_check.py`.

Runtime конкретного аккаунта, пользовательские книги, backup и персональные данные в release не входят.

## Авторизация

Рабочая схема сохранена без искусственных public-only костылей:

```bash
python3 scripts/pocketbook.py auth login
# PocketBook email: ...
# PocketBook password: [hidden]
```

Пароль читается через `getpass`, используется только для password-login и не сохраняется. После успешного входа локально хранится только access/refresh session с приватными правами.

Consumer API использует client interoperability values браузерного клиента PocketBook. Перед публикацией проверено, что эти технические значения по-прежнему публично поставляются самим web-клиентом PocketBook. Они не являются пользовательским password/access/refresh credential. Публичный repository не содержит ни одного пользовательского токена или session-файла.

## Соответствие production

Runtime-код public release совпадает с актуальным private production skill. Публичная часть добавляет только документацию, CI, license и release-gate tests/tools. Это сознательно лучше, чем поддерживать отдельную «облегчённую» реализацию, которая через пару недель начинает врать о фактическом поведении.

## Тесты

Production-derived suite содержит 123 теста. Публичный repository добавляет release-boundary regressions; актуальное общее число выводится обычным unittest и не зашито в CI.

Все тесты offline: mocks и synthetic fixtures, без реального аккаунта и live API.

## Неофициальный статус

Это community integration. Она не связана с PocketBook и не одобрена компанией. Consumer Cloud API может измениться.

## Перед публикацией

```bash
python3 -m unittest discover -s tests -v
python3 tools/release_check.py
python3 -m compileall -q scripts tests tools
git diff --check
```

Release checker проверяет текущий tree, но не делает историю Git волшебно безопасной задним числом. Реальные секреты нельзя коммитить даже временно.
