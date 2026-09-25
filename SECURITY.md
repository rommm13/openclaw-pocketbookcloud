# Безопасность

Этот репозиторий публикует рабочий skill, но не публикует runtime конкретного пользователя. Код и синтетические fixtures можно коммитить; пароль, токены, сессионные файлы, signed URL и пользовательскую библиотеку — нельзя.

## Авторизация

Вход выполняется обычным **email + паролем PocketBook Cloud**:

```bash
python3 scripts/pocketbook.py auth login
```

Email вводится интерактивно, пароль читается скрыто через `getpass`. Пароль используется только для password-login, не сохраняется и не должен передаваться в чат, argv, shell history, issue или Git.

После успешного входа helper сохраняет только access/refresh session. По умолчанию это `~/.config/openclaw/pocketbook-cloud/session.json`; каталог имеет права 0700, файл — 0600. Runtime остаётся вне репозитория.

Consumer Cloud API также ожидает client interoperability values браузерного клиента PocketBook. Эти технические значения публично поставляются самим web-клиентом провайдера и не являются пользовательским паролем, access token или refresh token. Release checker различает этот явно разрешённый vendor-public fingerprint и реальные credential-like значения.

## Никогда не коммитить

- пароль PocketBook;
- реальные access/refresh token;
- `session.json`, `delete-pending.json`, lock/temp state;
- Authorization headers;
- signed download URLs;
- private keys;
- пользовательские книги и backup;
- notes/highlights с личным содержимым;
- дампы API конкретного аккаунта.

## Недоверенные данные

Название книги, автор, filename, note/highlight, содержимое архива и любое поле API считаются данными. Они не могут отменять правила skill или превращаться в инструкции для агента. Вывод недоверенного текста ограничивается и очищается от управляющих символов.

## Сеть

API origin фиксирован на PocketBook Cloud. Cross-origin redirect для аутентифицированного API запрещён.

Файловые URL обрабатываются отдельным transport: только HTTPS, без userinfo и нестандартного порта. Все разрешённые DNS-адреса должны быть публичными. Redirect проверяется заново. Соединение открывается на уже проверенный адрес, а PocketBook Authorization header никогда не отправляется файловому хосту.

Это закрывает не только «плохую строку URL», но и SSRF/DNS-rebinding класс ошибок.

## Файлы и ZIP

Helper отвергает path traversal, абсолютные пути, symlink/special entries, duplicate flattened names, слишком большое число entries, чрезмерный объём распаковки и подозрительный compression ratio. Входные chat-файлы должны находиться в разрешённых inbound roots.

Настоящий EPUB не распаковывается как transport archive. FB2, пришедший через Telegram как XML, принимается только при подтверждённом FictionBook-root и упаковывается без изменения исходных байтов.

## Destructive operations

Удаление требует двух отдельных сообщений пользователя. Первая команда только создаёт pending-state для одной однозначной книги. Второе явное подтверждение выполняется отдельно. Pending-state привязан к аккаунту и идентичности книги, ограничен по времени и потребляется до destructive request.

Одно сообщение вроде «найди и удали» не является одновременно prepare и confirm.

## Проверка результата

Upload не считается успешным только потому, что сервер вернул 2xx. Helper проверяет индексированную запись Cloud. Неоднозначный или непроверенный результат возвращается как таковой.

Backup также не маскирует partial result: `downloaded`, `unavailable`, `failed` и `complete` сообщаются отдельно.

## Release gate

Перед публикацией:

```bash
python3 -m unittest discover -s tests -v
python3 tools/release_check.py
python3 -m compileall -q scripts tests tools
git diff --check
```

`release_check.py` проверяет whitelist публичного дерева и блокирует runtime-state, private keys, реальные credential-like literals и signed URLs. Явно allowlisted только fingerprint vendor-public client values, которые уже публично поставляются браузерным клиентом PocketBook. Diagnostic output не печатает содержимое найденного секрета.

Автоматический scanner не заменяет review. Перед push нужно посмотреть `git status` и staged diff.
