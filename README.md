# TTS Proxy — OpenAI-Compatible

Прокси-сервер, который принимает запросы в формате OpenAI TTS API и перенаправляет их в один из трёх TTS-бэкендов:

| Бэкенд | Тип | Описание |
|--------|-----|----------|
| **Fish Audio** | Облачный | [fish.audio](https://fish.audio) — качественный TTS с клонированием голоса |
| **ElevenLabs** | Облачный | [elevenlabs.io](https://elevenlabs.io) — продвинутый TTS с эмоциями |
| **VoiceBox** | Локальный | [voicebox.sh](https://voicebox.sh) — Qwen3-TTS, работает на вашем компьютере |

## Требования

- Python 3.10+

## Установка

```bash
# 1. Клонируйте проект
git clone https://github.com/oneevil/tts-proxy.git
cd tts-proxy

# 2. Создайте виртуальное окружение и активируйте его
python3 -m venv venv
source venv/bin/activate

# 3. Установите зависимости
pip install -r requirements.txt

# 4. Создайте файл .env из примера
cp .env.example .env

# 5. Отредактируйте .env — укажите ключи и голоса (см. раздел «Настройка»)
```

## Запуск через Docker

Образ доступен на [Docker Hub](https://hub.docker.com/r/oneevil/tts-proxy) для `linux/amd64`, `linux/arm64` и `linux/arm/v7`.

```bash
# 1. Скачайте docker-compose.yml и .env.example
curl -O https://raw.githubusercontent.com/oneevil/tts-proxy/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/oneevil/tts-proxy/main/.env.example

# 2. Создайте .env
cp .env.example .env
# отредактируйте .env

# 3. Запуск
docker compose up -d

# Логи
docker compose logs -f

# Остановка
docker compose down

# Обновление образа
docker compose pull && docker compose up -d
```

**Важно:** если используете VoiceBox (локальный), в `.env` замените:

```env
# Было (для запуска без Docker):
VOICEBOX_BASE_URL=http://127.0.0.1:17493

# Стало (для Docker):
VOICEBOX_BASE_URL=http://host.docker.internal:17493
```

`host.docker.internal` — это адрес вашего Mac из контейнера Docker.

## Настройка (.env)

### Выбор бэкенда по умолчанию

```env
TTS_BACKEND=fish          # или elevenlabs, или voicebox
```

### Fish Audio

1. Зарегистрируйтесь на [fish.audio](https://fish.audio)
2. Создайте API-ключ: [fish.audio/app/api-keys](https://fish.audio/app/api-keys/)
3. Скопируйте ID голоса из URL страницы модели: `fish.audio/m/{ID}`

```env
FISH_AUDIO_API_KEY=ваш_ключ
FISH_DEFAULT_MODEL=s2-pro
FISH_DEFAULT_VOICE=id_вашего_голоса
```

### ElevenLabs

1. Зарегистрируйтесь на [elevenlabs.io](https://elevenlabs.io)
2. API-ключ: Profile Settings → API Keys
3. Voice ID: перейдите в Voices → нажмите на голос → скопируйте Voice ID

```env
ELEVENLABS_API_KEY=ваш_ключ
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ELEVENLABS_DEFAULT_VOICE=voice_id_вашего_голоса
```

### VoiceBox (локальный)

1. Установите и запустите [VoiceBox](https://voicebox.sh)
2. Создайте голосовой профиль в интерфейсе VoiceBox (http://127.0.0.1:17493)
3. Скопируйте `profile_id` — его можно найти через API: `curl http://127.0.0.1:17493/profiles`

```env
VOICEBOX_BASE_URL=http://127.0.0.1:17493
VOICEBOX_DEFAULT_VOICE=profile_id_вашего_голоса
VOICEBOX_LANGUAGE=ru
VOICEBOX_ENGINE=qwen
VOICEBOX_MODEL_SIZE=1.7B
```

## Запуск

```bash
source venv/bin/activate   # если ещё не активировано
python main.py
```

Сервер запустится на `http://localhost:8000`.

## Подключение к Memo AI

1. Откройте **Settings** → **AI Services** → **TTS** → **OpenAI**
2. Укажите:
   - **Base URL:** `http://localhost:8000`  (без `/v1` на конце!)
   - **API Key:** `dummy` (любое значение — ключи берутся из `.env`)
   - **Model:** `tts-1` или `tts-1-hd`

## API

### Синтез речи

```
POST /v1/audio/speech
```

Тело запроса (формат OpenAI):

```json
{
  "model": "tts-1",
  "input": "Привет, мир!",
  "voice": "alloy",
  "response_format": "mp3",
  "speed": 1.0
}
```

### Примеры curl

```bash
# Использовать бэкенд по умолчанию (из .env)
curl http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"Привет, это тест."}' \
  --output speech.mp3

# Явно указать Fish Audio
curl http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"fish:s2-pro","input":"Привет!"}' \
  --output speech.mp3

# Явно указать ElevenLabs
curl http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"elevenlabs:eleven_multilingual_v2","input":"Привет!"}' \
  --output speech.mp3

# Явно указать VoiceBox
curl http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"voicebox:qwen","input":"Привет!"}' \
  --output speech.wav
```

## Выбор бэкенда per-request

Помимо глобальной настройки `TTS_BACKEND`, можно выбирать бэкенд в каждом запросе через поле `model`:

| model | Бэкенд | Детали |
|-------|--------|--------|
| `tts-1` | По умолчанию (.env) | Fish: s1, ElevenLabs: настроенная модель |
| `tts-1-hd` | По умолчанию (.env) | Fish: s2-pro, ElevenLabs: настроенная модель |
| `fish:s1` | Fish Audio | Модель s1 |
| `fish:s2-pro` | Fish Audio | Модель s2-pro |
| `elevenlabs:eleven_multilingual_v2` | ElevenLabs | Multilingual v2 |
| `elevenlabs:eleven_turbo_v2_5` | ElevenLabs | Turbo v2.5 |
| `voicebox:qwen` | VoiceBox | Engine: Qwen3-TTS |
| `voicebox:chatterbox` | VoiceBox | Engine: Chatterbox |
| `voicebox:chatterbox_turbo` | VoiceBox | Engine: Chatterbox Turbo |

## Голоса

В поле `voice` можно передать:

- **ID голоса напрямую** — reference_id (Fish), voice_id (ElevenLabs), profile_id (VoiceBox)
- **Стандартное имя OpenAI** (`alloy`, `echo`, `nova` и т.д.) — будет подставлен голос по умолчанию из `.env`

## Ограничения скорости

| Бэкенд | Диапазон speed | Примечание |
|--------|---------------|------------|
| Fish Audio | 0.5 — 2.0 | |
| ElevenLabs | 0.7 — 1.2 | Ограничение API ElevenLabs |
| VoiceBox | — | Не поддерживает параметр speed |

Если указать значение за пределами диапазона, прокси автоматически обрежет до допустимого.

## Эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/v1/audio/speech` | Синтез речи (OpenAI-совместимый) |
| GET | `/v1/models` | Список доступных моделей |
| GET | `/health` | Проверка состояния |
| GET | `/` | Информация о сервере |

## Структура проекта

```
tts-proxy/
  main.py            — основной сервер
  requirements.txt   — зависимости Python
  Dockerfile         — Docker-образ
  docker-compose.yml — Docker Compose конфигурация
  .dockerignore      — исключения для Docker
  .env.example       — пример конфигурации
  .env               — ваша конфигурация (не коммитьте!)
  README.md          — этот файл
```
