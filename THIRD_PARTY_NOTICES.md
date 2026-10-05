# Сторонние компоненты

| Компонент | Версия в поставке | Источник / лицензия |
| --- | --- | --- |
| Codex CLI | 0.160.0 | [openai/codex LICENSE](https://github.com/openai/codex/blob/main/LICENSE), Apache-2.0; bundled resources имеют собственные notices |
| xterm.js | 6.0.0 | [xterm.js LICENSE](https://github.com/xtermjs/xterm.js/blob/master/LICENSE), MIT |
| addon-fit | 0.11.0 | Пакет `@xterm/addon-fit`, MIT |
| aiohttp | 3.14.3 | [aiohttp LICENSE](https://github.com/aio-libs/aiohttp/blob/master/LICENSE.txt), Apache-2.0 |
| Python | 3.12 | [python.org](https://www.python.org/), PSF License |

Файлы LICENSE xterm.js и addon-fit загружаются вместе с JS/CSS в `static/vendor` внутри Docker-образа. Все файлы npm vendor каталога Codex, включая notices компонентов, сохраняются в `/opt/codex`. Системные пакеты устанавливаются из Debian и сохраняют package metadata и лицензионные сведения дистрибутива. Python transitive dependencies перечислены в `requirements.lock`.

Исходники сторонних компонентов не дублируются в Git-пакете; скачивание включено в Dockerfile. Для офлайн-переноса используйте экспорт готового образа.

Лицензия собственного кода выбирается владельцем репозитория. В этой поставке файл LICENSE для собственного кода автоматически не добавлялся.
