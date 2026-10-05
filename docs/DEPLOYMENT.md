# Развёртывание на другом сервере

## Требования

Linux x86_64/amd64 или aarch64/arm64, Docker Engine, Bash и Python 3.9+ на хосте. В контейнере используется Python 3.12. Автоматическая установка Docker предусмотрена для Ubuntu и Debian с systemd; для другой ОС установите Docker средствами её пакетов.

Для небольшого пространства исходное решение работало на VDS с 1 ГБ RAM. Для нового сервера практичнее выделить от 2 ГБ RAM и от 5 ГБ свободного диска под образ, сборочный cache и проекты. Это ориентир для развёртывания, а не гарантия для любых команд Codex: большие проекты могут требовать больше ресурсов. Лимиты приложения задаются отдельно.

При сборке необходим доступ к Docker Hub, репозиториям Debian, npm registry и PyPI. Для Codex требуется исходящий доступ к сервисам авторизации и провайдеру модели.

## Вариант 1. Новая установка из Git

На целевом сервере:

```bash
# Если Git ещё отсутствует на Ubuntu/Debian:
sudo apt-get update
sudo apt-get install -y git python3

git clone <URL-ВАШЕГО-РЕПОЗИТОРИЯ> codex-web
cd codex-web
sudo ./scripts/install-docker.sh
python3 scripts/manage.py init-env
```

`install-docker.sh` устанавливает Docker из официального apt-репозитория и включает его автозапуск. Уже существующий Docker скрипт проверяет и не заменяет. Конфликтующие пакеты автоматически не удаляются. Скрипт основан на [Docker: Ubuntu](https://docs.docker.com/engine/install/ubuntu/) и [Docker: Debian](https://docs.docker.com/engine/install/debian/).

Отредактируйте `.env`, если нужны другой порт, адрес или лимиты. Пароль генерируется автоматически, файл имеет права 600. Значения пишутся буквально, без кавычек, shell-команд и inline-комментариев. Чтобы оба способа запуска, Docker CLI и Compose, читали пароль одинаково, используйте URL-safe символы сгенерированного пароля и не вводите `$` в пароль.

```bash
./scripts/deploy.sh --dry-run
sudo ./scripts/deploy.sh
sudo python3 scripts/manage.py status
curl -f http://127.0.0.1:8080/healthz
```

`sudo` не нужен, если текущий пользователь уже имеет доступ к Docker. Если `.env` создан root, дальнейшие скрипты запускайте с доступом на чтение этого файла.

Порядок deploy: проверить Docker → собрать образ с ограничениями → создать volumes → запустить контейнер → дождаться healthcheck до 60 секунд. Повторный deploy здорового существующего контейнера не вызывает пересборку и пересоздание. Для обновления используйте `update.sh`.

Первый deploy запускает приложение как часть явно запрошенной установки. Автозапуск контейнера отключён (`restart=no`). После каждой перезагрузки ОС запуск выполняется вручную:

```bash
sudo ./scripts/start.sh
sudo ./scripts/stop.sh
sudo ./scripts/status.sh
```

Эти команды используют установленный контейнер, не собирают образ и не меняют пароль. Start/stop в старой установке также отключают ранее установленную автоматическую restart policy.

Откройте `http://<IP-сервера>:8080`, введите `WEB_PASSWORD` из `.env`. Нажмите «Войти в ChatGPT» и следуйте инструкциям терминала. При доступе через NAT или firewall провайдера входящий TCP-порт должен быть разрешён. Настройки firewall скрипты не меняют.

## Вариант 2. Установка из архива исходников

На рабочей машине создайте пакет:

```bash
python3 scripts/export-source.py ../codex-web-source.tar.gz
scp ../codex-web-source.tar.gz ../codex-web-source.tar.gz.sha256 user@new-server:~/
```

На новом сервере:

```bash
sha256sum -c codex-web-source.tar.gz.sha256
tar -xzf codex-web-source.tar.gz
cd codex-web
sha256sum -c SHA256SUMS
sudo ./scripts/install-docker.sh
python3 scripts/manage.py init-env
sudo ./scripts/deploy.sh
```

Архив включает исходники, документацию и скрипты. Реальных паролей, Git metadata, пользовательских volumes и авторизации в нём нет.

## Вариант 3. Готовый Docker-образ

Это позволяет избежать повторной сборки на маломощном VDS. Образ содержит зависимости, но не пользовательские volumes и `.env`. Архитектура целевого сервера должна совпадать с архитектурой образа.

На сервере, где образ уже собран:

```bash
./scripts/export-image.sh ../codex-web-image.tar.gz
scp ../codex-web-image.tar.gz user@new-server:~/
```

На целевом сервере после установки Docker и распаковки исходников:

```bash
docker load -i ../codex-web-image.tar.gz
python3 scripts/manage.py init-env
./scripts/deploy.sh --skip-build
```

Для загрузки через registry можно вместо архива выполнить `docker pull` и записать его имя в `IMAGE_NAME` в `.env`. Не публикуйте образ с вручную добавленными credentials.

## Параметры .env

| Параметр | По умолчанию | Назначение |
| --- | --- | --- |
| `WEB_PASSWORD` | Генерируется | Пароль web-интерфейса, минимум 16 символов |
| `WEB_BIND_ADDRESS` | `0.0.0.0` | IP привязки опубликованного порта |
| `WEB_PORT` | `8080` | Порт хоста; порт контейнера всегда 8080 |
| `CONTAINER_NAME` | `codex-web` | Имя контейнера |
| `IMAGE_NAME` | `codex-web:local` | Имя/тег собираемого или загружаемого образа |
| `WORKSPACE_VOLUME` | `codex-web-workspace` | Проекты |
| `CODEX_HOME_VOLUME` | `codex-web-home` | Настройки, авторизация и история CLI |
| `MEMORY_LIMIT` | `384m` | RAM приложения и всех его дочерних процессов |
| `CPU_LIMIT` | `0.75` | CPU quota приложения |
| `PIDS_LIMIT` | `128` | Максимум процессов/потоков контейнера |
| `BUILD_MEMORY_LIMIT` | `256m` | RAM для шагов Docker build |
| `BUILD_CPU_QUOTA` | `50000` | Квота build при периоде 100000 мкс |
| `CODEX_VERSION` | `0.160.0` | Версия CLI для следующей сборки |

Объём общего RAM+swap равен лимиту RAM: дополнительный swap контейнера выключен. Лимиты build относятся к RUN-шагам legacy builder; Docker daemon, download/extraction базового образа и дисковый cache работают вне этих cgroups. Скрипт не создаёт swap на хосте и не меняет системные лимиты других контейнеров.

Скрипты сборки явно используют `DOCKER_BUILDKIT=0`, потому что flags `--memory`/`--cpu-quota` не являются лимитами BuildKit. Если ваша версия Docker уже не поддерживает legacy builder, соберите образ на отдельной машине подходящей архитектуры и запустите с `--skip-build`. Команда `docker compose up --build` в этом проекте не используется: сначала `python3 scripts/manage.py build`, затем `docker compose up -d --no-build`.

Для отдельного окружения измените все три имени: контейнера и обоих volumes. Один volume нельзя использовать одновременно как workspace и home.

## Другой файл конфигурации

Глобальный параметр `--env-file` ставится **до** подкоманды:

```bash
python3 scripts/manage.py --env-file /etc/codex-web/instance.env init-env
python3 scripts/manage.py --env-file /etc/codex-web/instance.env deploy
python3 scripts/manage.py --env-file /etc/codex-web/instance.env status
python3 scripts/manage.py --env-file /etc/codex-web/instance.env start
python3 scripts/manage.py --env-file /etc/codex-web/instance.env stop
```

Shell-обёртки используют `.env` в корне проекта. Для нестандартного env-файла вызывайте `manage.py` напрямую.

## Защищённый доступ через SSH

Чтобы открыть интерфейс только через SSH-туннель, задайте `WEB_BIND_ADDRESS=127.0.0.1` и выполните deploy/update. На клиентской машине:

```bash
ssh -N -L 18080:127.0.0.1:8080 user@server
```

Откройте `http://127.0.0.1:18080`. Трафик между клиентом и сервером проходит внутри SSH. Для домена с HTTPS можно поставить TLS reverse proxy; он должен сохранять Host/port, поддерживать WebSocket Upgrade, иметь достаточный timeout и устанавливать Secure для cookie `codex_web`.

## Проверка после установки

```bash
python3 scripts/manage.py status
docker logs --tail 50 codex-web
docker exec codex-web codex --version
docker exec codex-web codex login status
```

При изменении `CONTAINER_NAME` используйте своё имя. Healthcheck не проверяет модель. Создайте Codex-сессию через браузер и отправьте короткое сообщение, чтобы проверить доступ к выбранной модели.
