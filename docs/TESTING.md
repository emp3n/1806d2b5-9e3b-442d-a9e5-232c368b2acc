# Карта тестов

361 тестов, 1 пропуск (интеграция с реальным бинарником без токена). Все тесты
дешевле запуска целиком:

```sh
python3 -m pytest tests -q                     # 361 passed, 1 skipped
CARD_INTEGRATION=1 python3 -m pytest tests -q  # + реальный cloudflared
```

## Что покрывает каждый файл

| Файл | Тестов | Ответственность |
|---|---|---|
| `test_proc.py` | 45 | `/proc` как источник: stat, rss, age, CPU-ставки, cmdline, `/ready`, `/metrics`, лог с colo и причинами, `Collector.state()` |
| `test_bootstrap.py` | 40 | токен и pidfile, запуск/стоп/рестарт туннеля, adopted-процесс, проба порта, публичный хост и ingress-port |
| `test_card_js.py` | 34 | разметка и JS карточки: уникальность имён, paintGeo/paintConnections, кеш флагов, MB/GB, verbs, fit(), отсутствие мёртвых селекторов |
| `test_statefeed.py` | 22 | один канал данных: формат payload, heartbeat, shard, атомарная запись фиксированной длины, отказы verb |
| `test_phone.py` | 13 | мобильный док, safe-area, скролл vs масштабирование, тач-взаимодействие |
| `test_live_binary.py` | 11 | реальный cloudflared в интеграционном режиме |
| `test_endpoints.py` | 10 | все адреса и контракты: state.json, /ready, /metrics, ipwho.is, disambiguate, локальные SVG, viewport и theme-color в head |
| `test_responsive.py` | 8 | три breakpoint'а, стекирование на телефоне, док, dvh, fit FLOOR,Reduced motion |
| `test_resources.py` | 6 | cpu.max / cpu.weight / cpuset / memory.max, дробные ядра и `cores_text()` |
| `test_palette.py` | 6 | три палитры, контраст всех текстовых пар в светлой и тёмной моде, статические токены |
| `test_edge_cases.py` | 24 | битые JSON/HTTP-ответы, /proc-отказы, загрузка бинарника, stop/restart |
| `test_helpers.py` | 21 | конфиг, cgroup, хелперы сэмплера, graceful degradation проб |
| `test_probe_branches.py` | 10 | success-пути проб и фейковое /proc |
| `test_proc_defensive.py` | 16 | битые /proc-файлы, запись launch, cgroup |
| `test_full_coverage.py` | 21 | последние защитные ветки: secrets, geo, cgroup, statefeed |
| `test_tunnel_branches.py` | 20 | start/stop/restart, swap, SIGKILL/pidfile error paths |
| `test_compact_card.py` | 13 | компактная карточка: нет подвала, чипы только с данными, own-IP резолвер |
| `test_versions.py` | 12 | файл выбора версии, GitHub tags API, скачивание и запуск нужного релиза |
| `test_version_picker.py` | 6 | контракт выпадающего списка, анимации, postMessage, мост приложения |
| `test_shard_and_edges.py` | 4 | шард при остановке, кратность коло, бейдж ×N |
| `test_version_flow.py` | 9 | выбор версии только скачивает, фоновая закачка, секреты/env, стиль списка |
| `test_timing_and_feedback.py` | 8 | без текстовыхх подписей, цветовая индикация, фоновый старт, ровные тики |
| `test_geo.py` | 5 | кеш ISO-кодов, фильтр приватных IP, отказ сети, cluster из disambiguate |
| `test_sessions.py` | 4 | сокеты cloudflared и server-side ingress, только ESTABLISHED, комбинированный счёт |

## Контракты, которые нельзя ломать

1. **Один канал данных.** Браузер читает только `app/static/state.json`.
   Внешние API вызываются сэмплером. Тест: `test_disambiguate_is_never_called_from_the_browser`.
2. **Фиксированная длина state.json.** Иначе Streamlit отдаёт урезанный ответ
   из-за Content-Length. Тест: `test_state_file_has_a_fixed_length_and_atomic_replace`.
3. **Три палитры × две моды**, минимум 4.5:1 на текст и 3:1 на фокус.
   Тест: `test_every_text_pair_clears_wcag_aa_in_both_modes`.
4. **Три ширины.** Нет переполнения на 320/360/390/430/600/768/1024/1440 px.
   Тесты: `test_responsive.py` + замеры в `STRUCTURE.md`.
5. **Токены и валидация.** Ни одного hex в компонентных правилах `theme.py`,
   ISO-код флага проверяется регуляркой до подстановки в URL.
   Тесты: `test_generated_css_covers_every_palette_and_mode`,
   `test_geo_paint_is_guarded_against_injected_codes`.

## Как добавлять тест

- Новое поле в `state.json` → `test_statefeed.py::test_payload_exposes...` и
  `test_card_js.py` (проверка, что JS его отрисовывает).
- Новый эндпоинт → `test_endpoints.py`: адрес, формат ответа, кто читает,
  и aging/кеш.
- Новый breakpoint → `test_responsive.py` + замер в браузере на границах.
- Новый цвет → `test_palette.py`: добавить пару в `PAIRS` с порогом контраста.

Изменения в `mcp_exec.py` требуют отдельного прогона
`python scripts/test_skill.py` в каталоге скилла Streamlit.
