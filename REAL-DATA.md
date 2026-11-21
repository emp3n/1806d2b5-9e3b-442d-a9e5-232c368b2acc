# Живые данные: откуда карточка берёт каждое число

Карточка в `redesign/` показывала синтетику. Теперь у неё есть второй источник —
настоящий процесс `cloudflared` на этой машине, с настоящим туннелем. Ниже: что
именно прочитано, каким файлом или URL, чем это проверяется и где предел
честности.

## Токен: первый отклонён, второй принят

Оба токена декодируются одинаково (`base64url` → JSON):

```
a = 6001f9e73311c4c3a7db20d186847d25        account tag
t = 7bd4235b-ec9b-434d-8478-18aec2ecb0c2    tunnel id
s = base64url-строка                        tunnel secret
```

Внутри `s` лежит ASCII, и вот здесь токены расходятся:

| Токен | `s` | Декодируется в | Край |
|---|---|---|---|
| первый | 48 символов | `1a1d4236-de33-43a7-bbe7-b660b3158143` — **одна** UUID-строка (36 байт) | `ERR Register tunnel error from server side error="Unauthorized: Invalid tunnel secret"` |
| второй | 96 символов | `f5b6383c-3e91-45c4-a2a8-6f61601545eb2ac152fe-3c6d-4737-9343-f4f6a56743bf` — **две** UUID-строки подряд (72 байта) | `INF Registered tunnel connection … location=…` ×4, `/ready` → `200`, `readyConnections: 4` |

Первый токен проходил транспорт (QUIC и TCP до `region1/2.v2.argotunnel.com`
PASS, pre-checks healthy) и отклонялся только на регистрации — то есть его
секрет был не той формы (одна половина вместо двух), и край это видел сразу.
Второй регистрирует четыре соединения. Причина видна и в метриках: у первого
`cloudflared_tunnel_tunnel_register_fail{error="server_error"}` рос до 39, у
второго счётчика нет вовсе.

Вывод для карточки: она ничего не знает про форму токена — она читает
`readyConnections` из `/ready` и адреса из лога, поэтому «0 of 4» с причиной и
«4 of 4» без причины — это одна и та же ветка кода на разных данных.

## Как запустить

```sh
cd /tmp/user/1000/opencode
curl -sSL -o cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared                       # sha256 97bb3b02…ab55e7, 2026.11.1
umask 077 && printf '%s' '<TOKEN>' > cf-token   # секрет — в файл, не в argv

./cloudflared tunnel \
  --metrics 127.0.0.1:20241 \
  --logfile /tmp/user/1000/opencode/cf.log \
  --pidfile /tmp/user/1000/opencode/cf.pid \
  --loglevel info \
  run --token-file /tmp/user/1000/opencode/cf-token
```

Флаги `--metrics`, `--logfile` и `--pidfile` — это флаги команды `tunnel`, а не
`tunnel run`: `cloudflared tunnel --metrics … run`, иначе `flag provided but not
defined` (проверено трижды). `--logfile` заодно переключает файл в JSON-строки.

Скрипт на всё это — `redesign/tools/connector.sh start|status|stop` (берёт
`CLOUDFLARED_BIN`, `CLOUDFLARED_TOKEN_FILE`, `CLOUDFLARED_RUNTIME`); `status`
печатает ровно те поля, что читает карточка.

Панель:

```sh
streamlit run redesign/main.py --server.port 8599
# ?source=live   (по умолчанию) — читает процесс
# ?source=sample — прежняя лабораторная синтетика
```

Живая лента поднимается внутри процесса Streamlit: поток-сэмплер и HTTP на
`127.0.0.1:8598` (`CARD_API_PORT` меняет порт), страница опрашивает
`/api/state` раз в секунду.

Открыть это не с самой машины — телефон, туннель — можно только через
`gateway.py`: туннель несёт один порт, а странице и ленте нужен один адрес.

```sh
redesign/tools/serve.sh start          # пара: шлюз на 8599, Streamlit на 8600
redesign/tools/serve.sh status         # шлюз / Streamlit / лента — что живо
redesign/tools/serve.sh stop
# порт меняется CARD_PUBLIC_PORT (шлюз) и CARD_API_PORT (лента)
# CARD_GATEWAY_LOG=1 печатает каждый маршрут: /api/state -> 8598
```

Туннель при этом не трогается: он по-прежнему ходит на `localhost:8599`, и
именно там теперь отвечает шлюз. Слушает он `::` (оба семейства): `cloudflared`
раскрывает `localhost` в `::1` первым, и шлюз на одном IPv4 отвечал ему
`connection refused` — в `cf.log` 52 такие строки, снаружи это «то обновляется,
то нет».

Файл токена держите вне `/tmp`: перезагрузка среды стирает `/tmp`, и вместе с
ним уезжает сам туннель (`530` на публичном адресе). `~/.local/state/apLLMe/`
для этого и существует — там уже лежит `cf-launch.json`. Если туннель правится в дашборде и у него есть
сопоставление по пути, шлюз можно не запускать вовсе — `/api/*` → `8598`,
остальное → `8599` делает сам cloudflared.

## Один адрес: почему на телефоне не обновлялось

Карточка получала адрес ленты `http://127.0.0.1:8598/api/state` — то есть
адрес в системе координат **сервера**, выданный **браузеру**. На машине, где
крутится лента, он верный, поэтому на десктопе всё работало. На телефоне
`127.0.0.1` — это телефон: соединение отбивается, `catch` в опросе ставил
атрибут и больше ничего не делал, и на экране оставался HTML, нарисованный
сервером при отдаче страницы. Замороженные `4 of 4 connections live`, `uptime`
и графики выглядят как живые — отличить невозможно, потому и «данные не
обновляются, а вроде бы правильные».

Проверено воспроизведением: запрос к `/api/state` отбивается
(`ERR_CONNECTION_REFUSED`), `data-link` становится `lost`, а `cpu` и `uptime`
не меняются и через 7 секунд (`repro-frozen.png`). Ровно та же картина, что на
телефоне.

Правило: адрес считает `server.feed_url(page_url)` по **адресу страницы** —
loopback остаётся loopback, любой другой хост получает свой origin, явный
`CARD_API_URL`/`?api=` перекрывает всё. Чтобы «свой origin» было чем ответить,
рядом живёт `gateway.py`: один порт, `/api/*` в ленту, остальное в Streamlit,
байты в обе стороны (поэтому websocket Streamlit проходит как есть),
`/api/gateway` отвечает сам.

```
phone ──https──> tunnel ──> gateway:8599 ──/api/*──> feed:8598
                                        └──всё───> streamlit:8600
```

Замеры в браузере (страница открыта по адресу, отличному от `localhost`, —
как у телефона):

| Что | Было | Стало |
|---|---|---|
| `RD_API` | `http://127.0.0.1:8598/api/state` | `http://<адрес страницы>/api/state` |
| подвал | `live · pid 694266`, не меняется | `live · pid 739880`, тикает |
| `uptime` за 12 с | 35m 1s → 35m 1s | 35m 56s → 36m 8s |
| стоп/пуск через шлюз | — | `Stopped · pid 694266`, затем `live · pid 739880`, `11s`, `0.38 %` |
| лента пропала | числа замирали молча | `feed unreachable`, `no feed · 127.0.0.1:8598`, числа 45 % |
| лента вернулась | нужна перезагрузка | возврат сам, ≤ 10 с |


## Проверка ровно той команды, что в задании

Запущено дословно, без `--metrics`, `--logfile` и `--pidfile`:

```sh
cloudflared tunnel run --token "$(cat cf-token)"     # дословно: --token <токен>
```

Что в логе (метрики при этом сами встают на `127.0.0.1:20241`, как и обещает
`--help`):

```
INF Starting tunnel tunnelID=7bd4235b-ec9b-434d-8478-18aec2ecb0c2   ← id из токена разобран
INF Settings: map[token:*****]                                      ← сам токен в логе скрыт
INF Generated Connector ID: fe34ed81-c118-49ad-8105-05f79ff4c4c3
INF Initial protocol quic
INF | UDP Connectivity  region1.v2.argotunnel.com  PASS  QUIC connection successful |
INF | TCP Connectivity  region1.v2.argotunnel.com  PASS  HTTP/2 connection successful |
INF Registered tunnel connection connIndex=0 … ip=198.41.200.113 location=fra15 protocol=quic
INF Registered tunnel connection connIndex=1 … ip=198.41.192.227 location=fra08 protocol=quic
INF Registered tunnel connection connIndex=2 … ip=198.41.192.167 location=fra03 protocol=quic
INF Registered tunnel connection connIndex=3 … ip=198.41.200.193 location=fra16 protocol=quic
```

`curl -s 127.0.0.1:20241/ready` → `{"status":200,"readyConnections":4,…}`, и
карточка на этой же секунде показала `4 of 4 connections live`, скрыв строку
причины. Без `--logfile` лог текстовый — парсер читает и его, и JSON.

Обратная сторона этой формы запуска: с `--token` секрет лежит в
`/proc/<pid>/cmdline` и виден любому процессу того же пользователя —

```
$ tr '\0' '\n' < /proc/519582/cmdline | grep -c '^eyJ'
1        # секрет виден любому процессу того же пользователя
```

Поэтому рабочий токен переложен в `cf-token` (права `600`), коннектор поднят с
`--token-file`, и в `argv` секрета больше нет — это отдельный тест. В наш API и
в HTML страницы токен не попадает ни в одной из форм: `grep -c eyJ` даёт `0` и
по пейлоаду, и по разметке.

## Поле за полем

Замеры ниже сняты с процесса `pid 531174` в 2026-11-20 00:0x UTC (туннель живой,
4 соединения, `uptime` 50 минут).

| На карточке | Поле состояния | Откуда | Проверка | Сейчас |
|---|---|---|---|---|
| `pid` в уведомлении действия | `pid` | скан `/proc/*/cmdline` (или `CLOUDFLARED_PID`, или `--pidfile`) | `pgrep -x cloudflared` | `531174` |
| Выбранная версия | `.temp/cloudflared.version` | дефолт `latest`, список из GitHub tags API | `curl https://api.github.com/repos/cloudflare/cloudflared/tags` | `2026.11.1` |
| Origin при остановке | `geo.own_endpoint()` | ipwho.is → ipapi.co → ipinfo.io, кеш на процесс | `curl https://ipwho.is/` | `34.19.100.134` |
| путь в полосе | `path` | `readlink /proc/<pid>/exe` | `readlink /proc/531174/exe` | `/tmp/user/1000/opencode/cloudflared` |
| `CPU` (число + линия) | `cpu`, `cpu_series` | сумма `run_time` (нс) по всем `/proc/<pid>/task/*/schedstat`, как скорость между двумя сэмплами; фолбэк — `/proc/<pid>/stat` поля 14+15 (`utime`+`stime`, USER_HZ=100) | `awk '{s+=$1} END{print s}' /proc/531174/task/*/schedstat` | на экране **0.30–0.43 %** (тиками это читалось бы как `0` или `1`) |
| `RAM` (число + линия) | `mem`, `mem_series` | `/proc/<pid>/statm` поле 2 (страницы) × `SC_PAGE_SIZE` | `awk '{print $2}' /proc/531174/statm` → `12999`×4096 | `53 243 904 B` = **50.8 MB** |
| `uptime` | `uptime` | `/proc/<pid>/stat` поле 22 (`starttime`, тики с загрузки) и `/proc/uptime` | `28395.84 − 2783453/100` | **9m 21s**, растёт каждую секунду |
| `version` | `version` | metrics `build_info{version="…"}` | `./cloudflared --version` → `2026.11.1` | `2026.11.1` (совпало) |
| `4 of 4 connections live` | `live` | `GET 127.0.0.1:20241/ready` → `readyConnections` | `{"status":200,"readyConnections":4,…}` | `4` |
| причина под строкой статуса | `note` | лог коннектора, `Register tunnel error from server side` — только пока соединений ноль | `grep 'Register tunnel error' cf.log` | пусто (туннель жив); у первого токена было `Unauthorized: Invalid tunnel secret` |
| чип `Origin IP` | `edge_ip` | `https://1.1.1.1/cdn-cgi/trace` → `ip=` (адрес, который видит край) | `curl -s https://1.1.1.1/cdn-cgi/trace` | `91.247.168.135` |
| чип `Edges` | `edge_names` | `location=` из строк `Registered tunnel connection` в логе коннектора — те края, где он реально встал | `grep -o '"location":"[a-z0-9]*"' cf.log \| sort \| uniq -c` | `fra08, ist03, fra03, fra12` |
| треды/состояние (в состоянии, не на карточке) | `threads`, `proc_state` | `/proc/<pid>/stat` поля 20 и 3 | `awk '{print $3, $20}' /proc/531174/stat` | `S` → `sleeping`, 13 тредов |
| режим работы | `data-link` в DOM | успех/провал `fetch('/api/state')` | консоль | `ok` |

Ещё два поля состояния, которых нет на карточке: `register_fail` (метрика
`cloudflared_tunnel_tunnel_register_fail`, сейчас `0`) и `connector`
(`connectorId` из `/ready`, сейчас `d5ca80e2-6bbd-49b4-812d-3fa1466eb06b`).

## Почему CPU не 0

`/proc/<pid>/stat` считает время тиками по 10 мс, а простаивающий коннектор
тратит доли процента: за секунду это 0 тиков — и карточка честно, но бесполезно
печатала `0.0 %` секундами подряд. Взято ядровое счётное поле с наносекундами:
`/proc/<pid>/task/*/schedstat`, поле 1 (`se.sum_exec_runtime`), **сумма по всем
тредам** — иначе главный тред показал бы 1/14 стоимости.

```
$ cat /proc/531174/task/*/schedstat | awk '{s+=$1} END{print s" ns"}'
10516483517 ns
```

Проверка, что это то же самое время, только точнее (окно 2.002 с):

```
тики  delta 1        → 0.5 % (цена одного тика 10 мс)
sched delta 6.851 мс → 0.34 %
```

Совпадает по величине и не совпадает по разрешению. На экране теперь
`0.30 %`, `0.41 %`, `0.43 %` — и цифра, и подпись `0.3 % of one core`, и линия.
Формат: ниже 1 % два знака (`0.32 %`), иначе один (`2.5 %`) — и в `live.py`
(`cpu_text`), и в браузере (`fmtValue`), чтобы первая отрисовка совпадала с
последующими.

## Что было сломано и как это поймано

Симптом: при наведении на графики не появлялась точка, а `CPU` всегда
показывал `0.0 %`. Одна причина на оба: в конце скрипта появился блок
«вписать карточку в короткое окно» с `var frame = document.querySelector('.rd-frame')`,
а `function frame(...)` — это сам цикл отрисовки. `var` всплывает, поэтому
`window.requestAnimationFrame(frame)` получал DOM-элемент:

```
TypeError: Failed to execute 'requestAnimationFrame' on 'Window':
           parameter 1 is not of type 'Function'.   at frame (about:srcdoc:662)
```

Кадр умирал на первом вызове: линия и цифры застывали на значениях первой
отрисовки (там CPU ещё `0.0`), а курсор не дорисовывался, потому что его
показ живёт в том же цикле. Переменная переименована в `frameEl`, добавлен
статический тест `test_no_variable_shadows_a_function` (ищет `function N` и
`var N` в одном IIFE) плюс `test_the_animation_loop_is_a_function_at_call_time`,
`test_the_hover_cursor_is_reachable_and_visible` и
`test_sub_percent_values_are_formatted_not_rounded_to_zero`.

Проверено в браузере после правки:

```
hover CPU   → cursor class "rd-cursor on", opacity 1, left 86.95px, top 18.80px,
              цифра 0.37 % (отсчёт под курсором), подпись 0.37 % of one core
hover RAM   → cursor on, rect x 381 (под курсором), 50.1 MB
release     → cursor off (opacity 0), is-scrubbing снят, цифра вернулась к текущей
живость     → d первого пути меняется между кадрами: M-2.52 18.79 → M-2.34 18.79
```

## Четыре места, где легко соврать — и что делает код

Лог коннектора дописывается между запусками, и одна и та же строка `connIndex=`
приходит дважды (с `location=` и без). Отсюда три решения:

- **Слот соединения, а не строка.** Строки `connIndex=N` схлопываются в один
  слот; последнее непустое значение поля побеждает, пустое не стирает
  заполненное. Иначе четыре соединения читались бы как восемь.
- **Сессия, а не файл.** Парсер режет лог по последней строке `Starting tunnel`
  и (когда в строке есть `"time"`) отбрасывает всё до старта процесса. Иначе
  `Invalid tunnel secret` от предыдущего запуска всплывало бы как причина у
  живого туннеля — это и было поймано на реальном `cf.log`.
- **`/proc/<pid>/fd/1`, а не только `--logfile`.** Команда из задания пишет лог
  редиректом (`… > cf-new.log`); `log_path()` смотрит `--logfile`, потом
  `readlink /proc/<pid>/fd/1` (если это настоящий файл, а не терминал/пайп), и
  только потом `CLOUDFLARED_LOG`. Панель поэтому не требует переменных
  окружения.
- **Причина только при нуле.** `note` заполняется лишь когда
  `readyConnections == 0`: пока туннель жив, ошибки отдельных ретраев не
  выдаются за инцидент.

## Чего машина не знает — и карточка не выдумывает

- **Бюджет памяти.** У процесса `memory.max = max` (cgroup без лимита), `cpu.max`
  тоже нет, поэтому 256 MB и «одно ядро» — это соглашение панели, а не лимит
  ядра. Если лимит появится, `live.py` прочитает `memory.max` и подпишет
  `100% of N MB` уже по нему (`proc_memory_limit`).
- **Локация — из лога, а не из trace.** `colo` в `1.1.1.1/cdn-cgi/trace` — это
  PoP *этого запроса*; на этой машине он приезжал то `FRA`, то `IST`, пока
  туннель стоял в `fra08/ist03/fra03/fra12`. Поэтому в чипе `Edges` — локации
  соединений из лога коннектора, а `Origin IP` — `ip=` из trace (то, что край
  видит как адрес выхода).
- **Перезапуски и обновления** (`policy_verdict`, история рестартов): этого нет
  ни в `/proc`, ни в метриках без Cloudflare API. В живом состоянии
  `restarts = []`, и карточка эти поля не рисует.
- **Начало линии.** Пока окно молодое, серия дополняется первым значением
  (плоская линия = «данных ещё нет»), а не шумом.
- **Короткое окно.** Если окно ниже карточки, кадр масштабируется
  (`scale(0.62…1)`) вместо обрезки: `740×640` → `0.892`, `390×660` → `0.95`,
  `1280×800` → без масштаба. Прокрутки нет ни на одном размере.

## Тесты

`redesign/tests`, stdlib + pytest, без сети для юнитов:

```
python3 -m pytest tests -q
  133 passed, 12 skipped in 8.4s   # без токена коннектора интеграционные пропускаются
  145 passed in 20.3s              # с токеном — все 12 идут по-настоящему
```

Что именно закреплено:

`test_proc.py` — разбор `/proc/<pid>/stat` (включая команду со скобками и
пробелами), `statm`, возраст по `starttime`, таблица `cpu_rate` (10 тиков/с → 10 %,
полное ядро → 100 %, простой → 0 %, первый сэмпл → среднее за жизнь),
`proc_cpu_ns` (сумма по тредам, исчезнувший тред, отсутствие дерева `task/`),
таблица `cpu_rate_ns` (5 мс за 1 с → 0.5 %, счётчик назад → 0, а не минус),
«тики не видят 0.3 %, наносекунды видят», формат `cpu_text` и то, что доля
процента доходит до карточки, поиск
процесса по скану и по pid-файлу, разбор `/metrics`, `/ready` (тело 503),
лога в JSON и в тексте, `trace`, **слоты соединений вместо строк**,
**отсечение прошлой сессии** (`Starting tunnel` + `since`), `log_path` по
`--logfile`, по `fd/1` и «пайп — не лог», схема состояния (все ключи, которые
читает `card.py`, есть и в синтетике, и в живом состоянии), живой путь `4 of 4`
с `data-status="up"`, отсутствие токена в состоянии/пейлоаде/HTML, «мёртвый
процесс → `found: False`», подпись бюджета.

`test_card_js.py` — инварианты скрипта карточки без браузера: ни одна
переменная не перекрывает функцию (та самая поломка цикла), цикл отрисовки
вызывается функцией, курсор наведения существует в разметке, красится и
двигается (`spark.cursor.classList.add('on')`, `left`/`top`), обработчики
`pointermove`/`pointerleave`/`keydown` на месте, цифра под курсором — это
отсчёт (`show(spark, spark.buf[index])`), а не интерполяция, и субпроцент
форматируется двумя знаками.

`test_phone.py` — телефон без браузера: рейл — `position: fixed` полоса внизу
с `backdrop-filter` и `env(safe-area-inset-bottom)`; плашка названия скрыта, имя
уходит в `aria-label`; на `(hover: none)` кружок не уезжает и не поворачивается;
`.rd-stage` листается (`overflow-y: auto`, `justify-content: flex-start` — иначе
колоночный флекс обрезает голову карточки); `box-sizing: border-box`, без
которого отступ прибавлялся к `100vh` и конец карточки был недостижим; полоса
прокрутки из токенов карточки; `--dock` как единственный источник высоты
полосы; `fit()` на телефоне выходит до масштабирования, а при нехватке места
ставит `rd-scroll`, а не жмёт ниже 0.62; скраббер на пальце
(`pointerdown`/`pointerup`/`pointercancel`, `touch-action: pan-y`).

`test_gateway.py` — адрес ленты и шлюз: матрица `feed_url` (`None` → loopback,
`localhost:8599` → loopback, `https://streamlit.idealis.cc` → свой origin,
`192.168.1.7:8600` → свой origin, `CARD_API_URL`/`?api=` перекрывают всё,
`ftp://` и пустая строка игнорируются), «удалённая страница никогда не получает
loopback», маршрутизация `/api/*` в ленту и всего остального в приложение,
`/api/gateway` мимо апстримов, тело и заголовок `X-RD-Action` у POST,
websocket-поток через шлюз байтами (101 + два фрейма), 502 с названным портом
для упавшей ленты и для упавшего приложения, две заявки в одном соединении,
`probe()`. Плюс проверка, что `app.py` больше не пишет
`http://127.0.0.1:{port}/api/state` руками.

`test_control.py` — кнопки: `SIGTERM` уходит ровно тому pid, который читает
сэмплер (не из запроса); остановка без процесса отвечает, а не молчит; `start`
запускает запомненную строку и отказывается, если что-то уже работает; команда,
умершая сразу, — это ошибка с кодом выхода, а не «запущено»; `restart` даёт новый
pid и один живой процесс; секрет из `--token` переезжает в файл `0600`, а
запомненная строка содержит `--token-file`; ключ `X-RD-Action` обязателен (403),
чужой не принимается, `GET` на роут — 405, тело с неизвестным глаголом — 400;
`found: false` в ответе несёт `last_action`, чтобы «остановлено вами» отличалось
от падения; зомби читается как отсутствие процесса (`test_a_zombie_reads_as_gone
_not_as_a_running_connector`), а запущенный ребёнок забирается родителем
(`test_a_launched_child_is_reaped_not_left_as_a_zombie`).

`test_live_binary.py` — запускает настоящий `cloudflared` с `--token-file`,
ждёт `/metrics` и проверяет: версия в метриках равна `cloudflared --version`;
процесс находится тем же сканом, что и в карточке; `exe` — тот бинарник, что
запустили; возраст растёт на ~2 с за 2 с; RSS > 5 MB и тредов ≥ 2; два сэмпла
дают реальную скорость CPU и `mem > 5 MB`; `0 ≤ live ≤ 4`, `connectorId` — UUID,
`edge_names` — коды локаций (или адрес); `Origin IP` — IPv4; в `argv` нет
секрета (`--token-file`), а в состоянии и пейлоаде нет строки токена;
`/api/state` отдаёт `pid`, `status_text`, `path` и заголовок
`Access-Control-Allow-Origin: *`; убитый процесс превращается в
`{"found": false}`.

Без бинарника или файла токена интеграционные тесты **пропускаются** (`skip`), а
не зеленеют на пустом месте: `CLOUDFLARED_BIN`, `CLOUDFLARED_TOKEN_FILE`.

## Безопасность

- Секрет живёт в файле с правами `600` и передаётся `--token-file`: в
  `/proc/<pid>/cmdline` его нет — это проверяет тест. С `--token` он виден
  любому, кто может читать `/proc`.
- Сборщик читает `cmdline` (чтобы найти процесс и `--logfile`), поэтому в API
  уходит только `pid`: `source_label = "live · pid N"`, без аргументов запуска.
- API слушает только `127.0.0.1`, отвечает двумя маршрутами (`/api/state`,
  `/health`) и не пишет ничего на диск.
- Кнопки действительно управляют процессом, но только одним: тем pid, который
  читает сэмплер. `POST /api/action` требует заголовок `X-RD-Action` с ключом,
  который печатается в HTML и не попадает в `/api/state`; тело несёт только
  глагол, номер процесса из запроса не берётся никогда. Лента с
  `Access-Control-Allow-Origin: *` отдаёт числа кому угодно, но нажать кнопку
  может лишь тот, кто получил страницу.
- Запомненная командная строка лежит в `~/.local/state/apLLMe/cf-launch.json`
  (`0600`), секрет в ней заменён на `--token-file`: файл не воспроизводит
  утечку, ради которой заводился `--token-file`.

## Кнопки: что именно посылается процессу

```
клик по питанию (живой режим)
  → POST http://127.0.0.1:8598/api/action
    headers: Content-Type: application/json, X-RD-Action: <ключ из HTML>
    body:    {"action":"stop"}
  → Supervisor.stop(): os.kill(pid, SIGTERM); ждём до 5 с; SIGKILL, если жив
  → sampler.refresh() — следующий опрос уже честный
  ← {"ok": true, "action": "stop", "pid": 623992, "forced": false}
```

Проверено на живой странице (2026-11-20, туннель поднят этим же процессом):

```
до        4 of 4 connections live · live · pid 623992 · CPU 0.36 % · 47.5 MB
stop      тост «Stopped · pid 623992», статус «stopped by you»,
          uptime/version/path/edges/origin → «—», CPU и RAM → «—», «no process»,
          подвал «live · no cloudflared process», кнопка «Start service»
start     тост «Started · pid 626562», 4 of 4, uptime 10s, CPU 0.37 %,
          соединения поднялись заново (fra15, ist05, fra20, ist05)
restart   первый клик «Click again» (is-armed), второй — «Restarted · pid 623992»,
          uptime с «9s», 4 of 4, в /proc остался один процесс
```

Остановленный коннектор висит в `/proc` зомби до `wait()` родителя. Это был
второй настоящий баг: коллектор видел `Z` как живой процесс, карточка
показывала «0 of 4 connections live» с кнопкой `Stop` и не давала себя поднять
(«no cloudflared process to stop»). Теперь `Z` (и `X`) — это `found: false`,
а супервизор держит `Popen` и забирает ребёнка в `stop()`/`start()`.

## Кнопка «стоп» на странице, которая сама едет через туннель

Проверено на живом домене: остановка коннектора с телефона гасит и страницу —
`no feed · streamlit.idealis.cc`, поле статуса `feed unreachable`, и вернуть
коннектор с телефона уже нельзя, только с машины. Карточка теперь это знает:
если адрес ленты — публичное имя (не loopback, не `192.168.*`, не `10.*`,
не `172.16–31.*`), первое нажатие только взводит кнопку и пишет
`This page comes through this tunnel — again stops it, and the page goes with
it`; второе в течение 4.2 с отправляет `stop`. На локальном адресе и в
локальной сети остановка идёт с первого нажатия — там гасить нечего.

Проверка на домене: одно нажатие → тост-предупреждение, `is-armed` на кнопке,
коннектор жив (`pid 766465`, `4 of 4 connections live`), через 4.2 с кнопка
вернулась в `Stop service`.

## Что происходит, когда процесс умирает и возвращается

Проверено на открытой странице, без перезагрузки:

```
pid 513846 → connector.sh stop → страница: «no cloudflared process» (data-link=gone,
            статус приглушён) → connector.sh start → pid 515459, uptime 15s,
            причина снова на месте, data-link=ok
pid 525123 → 531174 (перезапуск с --token-file) → карточка сама перешла на новый
            pid, uptime начался с «2s», дальше 4 of 4
```

Лента переживает и `{"found": false}` от API (процесса нет), и обрыв fetch
(`data-link=lost`): карточка не держит последнее приятное число, а говорит, что
процесса нет.
