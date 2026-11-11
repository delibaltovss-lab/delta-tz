# Codex и GitHub: проект DELTA

## Mac
Скачать официальное приложение ChatGPT для macOS: https://chatgpt.com/download/ .
В приложении есть Codex. Требуются macOS 14+, Intel либо Apple Silicon.

## Подключение к репозиторию
1. Войти в ChatGPT, выбрать Codex.
2. Создать облачное окружение и при необходимости подтвердить отдельный доступ GitHub для Codex.
3. Выбрать `delibaltovss-lab/delta-tz`.
4. Попросить Codex прочитать `AGENTS.md`, установить `requirements.txt` и выполнить `python test_offline.py`.
5. После проверки попросить открыть PR с исправлениями вместо изменения `main` напрямую.

## VPS (отдельный шаг)
После клонирования репозитория под выделенным SSH-ключом только для чтения:
```bash
git clone https://github.com/delibaltovss-lab/delta-tz.git ~/apps/delta-tz
cd ~/apps/delta-tz
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python test_offline.py
```

Для периодического обновления можно использовать `scripts/vps_sync.sh` и cron, но сначала настроить авторизацию и проверить вручную. Скрипт только подтягивает код, реальную торговлю не запускает. Для приватного репозитория используйте read-only deploy key и SSH, не сохраняйте пароли в код.

**Безопасность:** сейчас репозиторий публичный. Перед добавлением конфиденциальных файлов сделайте его приватным. Торговые API-ключи не публикуйте.
