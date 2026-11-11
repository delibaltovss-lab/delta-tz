# DELTA — «Матрёшка»

Исследовательский бэктестер торговли по подтверждённым трендовым линиям. **Не торговый бот: реальных ордеров и API-ключей здесь нет.**

## Параметры стратегии

| Тренд | Вход | Первоначальный стоп |
|---|---|---|
| 1M | 1W | 1,5 × ATR(14) недели |
| 1W | 1D | 1,5 × ATR(14) дня |
| 4H | 1H | 1,5 × ATR(14) часа |
| 1H | 15m | 1,5 × ATR(14) 15 минут |

Инструменты: BTCUSDT, ETHUSDT, SOLUSDT. Исследовательский риск $2 на сделку. Сравниваем выходы: `rr2`, `trend_break`, `atr_trail`. Комиссии и проскальзывание моделируются; фандинг нет.

## Быстрый старт
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python test_offline.py
```

Полный бэктест (скачивание публичной истории Binance USD-M):
```bash
python backtest.py --out results
```

Результат: `results/trades.csv`, `results/summary.csv`, `results/methodology.json`.

**Реальный исторический тест ещё не выполнялся.** Четыре локальных офлайн-проверки на синтетических данных прошли, но не подтверждают прибыльность. Проверки через GitHub Actions доступны в разделе Actions.

## Для Codex и VPS

- [Инструкция для Codex](AGENTS.md)
- [Описание методики](README_RU.md)
- [Подключение GitHub, Mac и VPS](GITHUB_SETUP_RU.md)
- [Другая инструкция Codex](CODEX_SETUP_RU.md)
- [.github/workflows/offline-check.yml](.github/workflows/offline-check.yml) запускает офлайн-тесты по push и PR.
- [scripts/vps_sync.sh](scripts/vps_sync.sh) выполняет только `git pull --ff-only` и не запускает сделки.

⚠️ Репозиторий сейчас **публичный**. Не публикуйте пароли, `.env` и API-ключи.
