# Дерево проекта карточки cloudflared

Этот каталог — **один деплой**: Streamlit-приложение, внутри которого живёт
карточка, и фоновый сэмплер, который её кормит. Ниже — полная карта файлов,
потоки данных и точки входа. Документ является источником истины по структуре;
перед изменением любого модуля найдите его строку в таблице.

## Дерево

```
redesign/
├── main.py                 Launcher. Два режима: `st.App` (python main.py) и
│                           inline runpy (streamlit run main.py). UI здесь нет.
├── app.py                  Сборка страницы: query-параметры, состояние,
│                           <head> (charset, viewport, theme-color), iframe
│                           srcdoc с карточкой. Единственный автор HTML-обвязки.
├── core/                   ПАКЕТ ДАННЫХ. Ни одного UI-импорта; всё, что происходит
│   ├── __init__.py         ниже, читается сэмплером и публикуется в один JSON.
│   ├── bootstrap/          ПАКЕТ: __init__ даёт плоский API, слои:
│   │   ├── config.py       токен, host/port, desired state, pidfile-конфиг
│   │   ├── binary.py       asset/download/version, локальный бинарник
│   │   ├── probes.py       /ready, /metrics, public, /proc-пробы
│   │   └── tunnel.py       Tunnel, start/stop/restart/adopt, ensure
│   ├── live/               ПАКЕТ: __init__ даёт плоский API, слои:
│   │   ├── proc.py         /proc: stat, rss, cpu, cmdline, pid-сканирование
│   │   ├── sources.py      /ready, /metrics, log, trace — парсеры и _http
│   │   └── collector.py    Collector.state() и idle_state()
│   ├── statefeed.py        Единственный канал: Sampler, atomic write state.json,
│   │                       INTERVAL=0.5, to_payload() -> плоский JSON.
│   ├── geo.py              ipwho.is (кеш по IP) и disambiguate -> cluster/shard-3.
│   ├── sessions.py         connections(): сокеты реплики + server-side ingress.
│   ├── resources.py        Лимиты контейнера из cgroup: cpu.weight, memory.max.
│   └── demo_data.py        Воспроизводимые демо-данные (?source=sample).
├── ui/                     ПАКЕТ ИНТЕРФЕЙСА. Никакого сэмплирования и сети.
│   ├── __init__.py
│   ├── card.py             Разметка + JavaScript: paintGeo, paintConnections,
│   │                       опрос 500 мс, verbs, fit(), sparkline, MB/GB.
│   ├── theme.py            ВСЁ CSS: токены + статические правила + breakpoints.
│   └── palette.py          Три палитры, шкалы Radix, semantic_tokens(), контраст.
├── bootstrap.py            Совместимость: алиас на core.bootstrap (3 строки).
├── live.py                 Совместимость: алиас на core.live.
├── statefeed.py            Совместимость: алиас на core.statefeed.
├── geo.py, sessions.py, ...и прочие алиасы на core/*.
├── card.py, theme.py, palette.py   Алиасы на ui/*.
├── mcp_exec.py             Удалённый исполнитель MCP (vendored, не трогать без нужды).
├── static/
│   ├── state.json          RUNTIME STATE — генерируется, в git не коммитится.
│   ├── flags/*.svg         271 локальный флаг flag-icons (MIT) + LICENSE.txt.
│   └── *.woff2             4 шрифта, встраиваются как data URL в theme.py.
├── tests/                  См. docs/TESTING.md — карта всех тестов.
├── docs/
│   ├── STRUCTURE.md        Этот файл.
│   ├── TESTING.md          Карта тестов.
│   ├── TIMEOUTS.md         Аудит всех тайм-аутов.
│   ├── COVERAGE.md         Измеренное покрытие (87%, сырой вывод в .txt).
│   └── TESTING.md          Что тестируется и как добавлять тесты.
├── .uimix/DESIGN-BRIEF.json  Бриф дизайна (style/palette/modes/density/a11y).
├── requirements.txt        streamlit==1.64.0 — единственная зависимость.
└── secrets.example.toml    Шаблон секретов (токен туннеля).
```

## Поток данных

```
   cloudflared process ── /ready (readyConnections) ─┐
        │                 /metrics (gauges)          │
        │                 лог (colos, причины)        ├─> live.Collector.state()
        │                 /proc (cpu, rss, PID)      │        │
        └── socket-иноды ──> sessions.connections() ─┘        │
                                                               v
   ipwho.is (geo, 1 раз на IP) ──> geo.lookup() ─────> statefeed.to_payload()
   disambiguate (cluster) ───────> geo.shard() ──────>        │
   cgroup (ядра/RAM) ────────────> resources ─────────>        │
                                                               v
                                          static/state.json (атомарно, 0.5 с)
                                                               │
                                          карточка poll() каждые 500 мс
                                                               │
                                          apply() -> paintGeo/paintConnections
```

Браузер **никогда** не ходит во внешние API: вся география резолвится на
сервере и публикуется в `state.json`. Флаги — локальные SVG, не CDN и не PNG.

## Эндпоинты и адреса

| Адрес | Кто читает | Что отдаёт |
|---|---|---|
| `app/static/state.json` | карточка (poll 500 мс) | плоский JSON: cpu/mem/live/connections/shard/geo/tunnel |
| `http://127.0.0.1:20241/ready` | `live.Collector` | `readyConnections`, `connectorId`, `status` (503 — норма) |
| `http://127.0.0.1:20241/metrics` | `live.Collector` | `cloudflared_*` метрики |
| `https://ipwho.is/<ip>` | `geo.lookup()` | ISO-2, страна, регион, город |
| `https://<subdomain>.streamlit.app/api/v2/app/disambiguate` | `geo.shard()` | `cluster: "shard-3"` |
| `https://<host>/app/static/flags/<cc>.svg` | браузер(card) | локальный SVG 4:3 |
| `/api/v2/app/status` (диагностика) | оператор | версия, статус приложения |

Публичный домен и ingress-порт не захардкожены: `bootstrap.public_hostname()`
и `bootstrap.ingress_port()` читают конфигурацию/окружение.

## Адаптивность

| Ширина | Поведение |
|---|---|
| `< 520px` (телефон) | каждый чип в отдельной строке, слева; каждая строка чипов — колонка; город и коды переносятся свободно; док снизу |
| `520–760px` (телефон/малый планшет) | док снизу, карточка до 432px, пары Origin/shard и edges/connections в строку |
| `> 760px` (планшет/ПК) | карточка 392px, рейл справа, пары в строку; короткое окно масштабируется `fit()` до `FLOOR=0.62`, ниже — скролл |

Все три варианта проверены в браузере на 320/360/390/430/600/768/1024/1440px:
переполнения нет, значения читаемы, city и коды не обрезаются.

## Как править без поломок

1. **Цвет** — только в `palette.py` (примитив/семантика). В `theme.py` не должно
   появляться новых hex; компоненты читают `var(--роль)`.
2. **Новое поле в карточке** — добавить в `live.py:Collector.state()`,
   `statefeed.to_payload()`, разметку `card.py` и JS `apply()`; затем тест в
   `tests/test_statefeed.py` + `tests/test_card_js.py`.
3. **Новый media-запрос** — только в `theme.py`, с тестом в `tests/test_phone.py`
   или `tests/test_responsive.py`.
4. **Новый источник данных** — отдельный модуль с Docstring-контрактом и кешем,
   как `geo.py`; сэмплер дёргает его в фоновом потоке, не браузер.
5. **Рuntime state** — `static/state.json` и `.temp/` не коммитить.
