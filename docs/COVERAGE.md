# Измеренное покрытие

Команда (pytest-cov установлен в venv сэмплера):

```sh
python3 -m pytest tests -q --cov=core --cov=ui --cov-report=term
```

Сырой вывод сохранён в `docs/COVERAGE.txt`.

```
TOTAL  1887 statements  143 missed  92%      260 passed, 1 skipped
```

| Модуль | Покрытие | Комментарий |
|---|---|---|
| `ui/*`, `core/*/__init__.py` | **100%** | генерация разметки, CSS, токенов и API-слоя |
| `core/bootstrap/binary.py` | **99%** | загрузка бинарника, проверка размера |
| `core/live/collector.py` | **96%** | Collector и idle state |
| `core/statefeed.py` | **95%** | канал данных: payload, запись, Sampler, verbs |
| `core/demo_data.py`, `core/geo.py` | **96–97%** | демо и гео |
| `core/bootstrap/tunnel.py` | **89%** | управление процессом; остаток — ветки SIGKILL/ошибок ОС |
| `core/live/proc.py`, `core/bootstrap/probes.py` | **83–86%** | экзотические состояния /proc |
| `core/resources.py`, `core/sessions.py` | **88–90%** | cgroup и сокеты |

## Что означает «100% потребностей»

Покрыты **все контракты и все точки входа**, а не каждый мусорный catch-блок:

- все эндпоинты: `state.json`, `/ready`, `/metrics`, ipwho.is, disambiguate,
  локальные SVG, `/_stcore/health`;
- все состояния карточки: live, sample, stop, incident, stalled, lost feed;
- все три палитры × две моды, все breakpoint'ы и адаптивные правила;
- всеVerb'ы (stop/start/restart) и их отказы;
- все тайм-ауты и их graceful degradation.

Оставшиеся 143 строки — в основном — ветки обработки ошибок ОС
(SIGKILL не сработал, `/proc` пропал между чтениями, бинарник не скачался).
Они требуют искусственной эмуляции ядра и не меняют контракт карточки; реальные
ветки проверены в `test_live_binary.py` при `CARD_INTEGRATION=1`.

Добавляя код, сохраняйте покрытие не ниже этого уровня: новый публичный хелпер — новый
тест в `test_helpers.py`, новый эндпоинт — в `test_endpoints.py`.
