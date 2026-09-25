# Changelog

## 1.0.0 — 2026-09-25

Первый публичный релиз PocketBook Cloud skill для OpenClaw.

- актуальная модульная реализация `pocketbook_lib`;
- авторизация PocketBook Cloud по email + скрытому паролю;
- библиотека, поиск, reading progress, notes/highlights;
- upload/download и batch preflight;
- подтверждение больших операций отдельным пользовательским ходом;
- безопасная обработка EPUB, FB2, FB2.ZIP и transport ZIP;
- Telegram XML-as-FB2 handling без реконструкции текста;
- chat delivery через настоящий outer ZIP;
- backup с точным partial-result reporting;
- двухходовое подтверждение удаления;
- fail-closed API contract validation;
- HTTPS download transport без утечки Authorization;
- private atomic session state и concurrent refresh lock;
- RU/EN документация;
- CI для Python 3.11/3.12;
- public release gate для runtime/secrets/signed URLs.
