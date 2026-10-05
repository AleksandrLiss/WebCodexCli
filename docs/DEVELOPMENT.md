# Разработка и публикация в Git

## Состав репозитория

```text
codex-web/
├── server.py                   # HTTP, авторизация, PTY и WebSocket
├── static/                     # Интерфейс; vendor загружается при Docker build
├── Dockerfile / compose.yaml   # Образ и альтернативный запуск Compose
├── requirements.txt            # Прямая Python-зависимость
├── requirements.lock           # Снимок всех Python-зависимостей
├── download-assets.py          # Зафиксированные npm-компоненты
├── .env.example                # Шаблон без пароля
├── scripts/                    # Start, stop, status, deploy, update, backup, restore, export, validate
├── tests/                      # Проверки скриптов и HTTP/PTY smoke
└── docs/                       # Техническая документация
```

Не включаются: реальный `.env`, cache Python, backups, Docker volumes, credentials, логи, архивы релизов. `.gitignore` и `.dockerignore` настроены отдельно. `.env.example` содержит пустой пароль, который генерирует `init-env`.

## Первое добавление в Git

В поставляемом каталоге может быть уже инициализирован `.git`; архив исходников его не содержит. После распаковки:

```bash
cd codex-web
git init -b main
git config user.name "ВАШЕ ИМЯ"
git config user.email "ВАШ EMAIL"
git add .
git status --short
git diff --cached --stat
git commit -m "Initial import of Codex Workspace"
git remote add origin <URL-ВАШЕГО-РЕПОЗИТОРИЯ>
git push -u origin main
```

Адрес remote и Git identity выбирает владелец; скрипты не публикуют код и не записывают чужую identity. Если `.git` уже существует, повторный `git init` не требуется.

Проверка исключения секретов:

```bash
git check-ignore .env auth.json backups/example dist/example
git ls-files .env auth.json
```

Последняя команда не должна выводить файлов. Не используйте `git add -f` для credentials. Данные проекта в Docker volumes публикуются в свои репозитории отдельно.

## Проверки без работающего сервера

Требуются Bash и Python 3.9+. Node.js нужен только для проверки синтаксиса клиента:

```bash
./scripts/validate.sh
```

Проверяются синтаксис Bash/Python и сценарии скриптов: сохранение пароля, права 600, literal dotenv, idempotent start/stop/deploy, отключение legacy restart policy, порядок сборки и остановки, rollback, возврат сервиса при ошибке backup, checksums, запрет перезаписи и очистка экспортируемых исходников. Docker в unit tests заменён test double; настоящие containers, volumes и модель не используются.

Если Node отсутствует на хосте, проверить клиент можно в имеющемся образе:

```bash
docker exec codex-web node --check /app/static/app.js
```

После изменения локального `static/app.js` эта команда проверит только файл работающего образа. Для проверки локального файла без пересборки:

```bash
docker run --rm --network none --memory=64m --cpus=0.25 \
  --mount type=bind,src="$PWD/static",dst=/source,readonly \
  codex-web:local node --check /source/app.js
```

## Проверка реального сервиса

```bash
docker cp tests/smoke.py codex-web:/tmp/smoke.py
docker exec codex-web python /tmp/smoke.py
```

Тест создаёт одну временную Bash-сессию. Если заняты оба места, сначала завершите ненужную сессию. Проверяются отдача frontend assets, auth, Origin, ввод/вывод, resize, Ctrl C, повторное подключение, logout и удаление процесса. Модель не вызывается. Браузерный rendering этим тестом не проверяется.

Отдельная проверка миграции на временных Docker volumes:

```bash
python3 tests/integration-storage.py
```

Она использует имеющийся образ, фиксированные малые лимиты и уникальные имена `codex-web-storage-test-*`, не подключает volumes рабочего приложения и удаляет собственные тестовые ресурсы в finally.

Проверка ручного start/stop на отдельном временном web-контейнере:

```bash
python3 tests/integration-manual.py
```

Тест использует 96 МБ RAM и 0.5 CPU, не подключает production volumes и не публикует порты. Проверяет переход со старой restart policy, healthcheck и повторяемость команд; обращения к модели отсутствуют.

## Изменения и версии

Runtime API и поведение описаны в [API.md](API.md). Сессии находятся в памяти единственного процесса сервера; горизонтальное масштабирование или несколько aiohttp workers без изменения архитектуры не поддерживаются.

`requirements.lock` фиксирует проверенный набор Python-пакетов. Для изменения зависимостей обновляйте его вместе с `requirements.txt` и выполняйте smoke-тест в пересобранном образе. Base image и apt packages не закреплены на immutable snapshot, поэтому сборка не гарантирует побитово одинаковый образ через длительное время.

Версия Codex задаётся build argument `CODEX_VERSION` через `.env`; xterm/addon-fit фиксируются в `download-assets.py`. Изменение версии CLI требует проверить его flags и структуру npm package. Ресурсы npm проходят checksum по metadata, большой archive обрабатывается потоково.

## Экспорт исходников

```bash
python3 scripts/export-source.py ../codex-web-source.tar.gz
```

Exporter использует allowlist исходных файлов/директорий, исключает секреты и cache, проверяет отсутствие текущего web-пароля в исходниках. Результат содержит `codex-web/SHA256SUMS` с checksum каждого файла; рядом создаётся checksum всего tar.gz. Это контроль целостности, не цифровая подпись.

Docker-образ и пользовательские данные экспортируются другими скриптами. Образ можно получить через `scripts/export-image.sh`, данные — через `scripts/backup.sh`; backup содержит credentials и не входит в пакет для Git.
