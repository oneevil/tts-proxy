import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tts-proxy")

# ══════════════════════════════════════════════════════════════════════════
# Конфигурация
# ══════════════════════════════════════════════════════════════════════════

# Бэкенд: "fish", "elevenlabs" или "voicebox"
TTS_BACKEND = os.getenv("TTS_BACKEND", "fish").lower()

# Fish Audio
FISH_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_BASE_URL = os.getenv("FISH_AUDIO_BASE_URL", "https://api.fish.audio")
FISH_DEFAULT_MODEL = os.getenv("FISH_DEFAULT_MODEL", "s2-pro")
FISH_DEFAULT_VOICE = os.getenv("FISH_DEFAULT_VOICE", "")

# ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_BASE_URL = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_DEFAULT_VOICE = os.getenv("ELEVENLABS_DEFAULT_VOICE", "21m00Tcm4TlvDq8ikWAM")

# VoiceBox (локальный)
VOICEBOX_BASE_URL = os.getenv("VOICEBOX_BASE_URL", "http://127.0.0.1:17493")
VOICEBOX_DEFAULT_VOICE = os.getenv("VOICEBOX_DEFAULT_VOICE", "")
VOICEBOX_LANGUAGE = os.getenv("VOICEBOX_LANGUAGE", "ru")
VOICEBOX_ENGINE = os.getenv("VOICEBOX_ENGINE", "qwen")
VOICEBOX_MODEL_SIZE = os.getenv("VOICEBOX_MODEL_SIZE", "1.7B")

# Прокси
PROXY_PORT = int(os.getenv("PROXY_PORT", "8000"))
DOCS_ENABLED = os.getenv("DOCS_ENABLED", "false").lower() == "true"

http_client: httpx.AsyncClient | None = None


def _validate_config():
    """Проверяем конфиг при старте и выдаём предупреждения."""
    if TTS_BACKEND not in ("fish", "elevenlabs", "voicebox"):
        logger.error(f"Unknown TTS_BACKEND={TTS_BACKEND}. Use: fish, elevenlabs, voicebox")
        raise SystemExit(1)

    if TTS_BACKEND == "fish" and not FISH_API_KEY:
        logger.warning("TTS_BACKEND=fish but FISH_AUDIO_API_KEY is not set")
    if TTS_BACKEND == "elevenlabs" and not ELEVENLABS_API_KEY:
        logger.warning("TTS_BACKEND=elevenlabs but ELEVENLABS_API_KEY is not set")
    if TTS_BACKEND == "voicebox" and not VOICEBOX_DEFAULT_VOICE:
        logger.warning("TTS_BACKEND=voicebox but VOICEBOX_DEFAULT_VOICE is not set")

    logger.info(f"Config: backend={TTS_BACKEND}, docs={'on' if DOCS_ENABLED else 'off'}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    _validate_config()
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
    )
    logger.info(f"TTS Proxy started on port {PROXY_PORT}")
    yield
    await http_client.aclose()
    logger.info("TTS Proxy stopped")


app = FastAPI(
    title="TTS OpenAI-Compatible Proxy",
    description="Proxies OpenAI TTS API to fish.audio, ElevenLabs or VoiceBox",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

# ══════════════════════════════════════════════════════════════════════════
# Маппинг голосов — алиасы OpenAI → ID бэкенда
# ══════════════════════════════════════════════════════════════════════════

OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

# Fish Audio: OpenAI voice name → fish.audio reference_id
FISH_VOICE_MAP: dict[str, str] = {
    # "alloy": "some-fish-model-id",
}

# ElevenLabs: OpenAI voice name → ElevenLabs voice_id
ELEVENLABS_VOICE_MAP: dict[str, str] = {
    # "alloy": "21m00Tcm4TlvDq8ikWAM",
}

# VoiceBox: OpenAI voice name → VoiceBox profile_id
VOICEBOX_VOICE_MAP: dict[str, str] = {
    # "alloy": "658e5bfb-4f72-4b52-afcb-61d352906761",
}

# ══════════════════════════════════════════════════════════════════════════
# Маппинг форматов
# ══════════════════════════════════════════════════════════════════════════

FISH_FORMAT_MAP = {
    "mp3": "mp3",
    "opus": "opus",
    "aac": "mp3",
    "flac": "wav",
    "wav": "wav",
    "pcm": "pcm",
}

ELEVENLABS_FORMAT_MAP = {
    "mp3": "mp3_44100_128",
    "opus": "opus_48000_64",
    "aac": "mp3_44100_128",
    "flac": "mp3_44100_128",
    "wav": "wav_44100",
    "pcm": "pcm_44100",
}

CONTENT_TYPE_MAP = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "aac": "audio/mpeg",
    "flac": "audio/mpeg",
}


class OpenAISpeechRequest(BaseModel):
    model: str = "tts-1"
    input: str
    voice: str = "alloy"
    response_format: str = "mp3"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


# ══════════════════════════════════════════════════════════════════════════
# Хелперы
# ══════════════════════════════════════════════════════════════════════════

def get_api_key(env_key: str, backend_name: str) -> str:
    """Всегда берём ключ из .env — у каждого бэкенда свой ключ."""
    if env_key:
        return env_key
    raise HTTPException(
        status_code=401,
        detail=f"No API key for {backend_name}. Set it in .env file.",
    )


def get_backend(body: OpenAISpeechRequest) -> str:
    """Определяет бэкенд. Модель может переопределять глобальную настройку."""
    model = body.model.lower()
    if model.startswith("fish:") or model in ("fish",):
        return "fish"
    if model.startswith("elevenlabs:") or model in ("elevenlabs", "eleven"):
        return "elevenlabs"
    if model.startswith("voicebox:") or model in ("voicebox", "vb"):
        return "voicebox"
    return TTS_BACKEND


# ══════════════════════════════════════════════════════════════════════════
# Fish Audio бэкенд
# ══════════════════════════════════════════════════════════════════════════

async def synth_fish(body: OpenAISpeechRequest) -> StreamingResponse:
    api_key = get_api_key(FISH_API_KEY, "fish.audio")
    fish_format = FISH_FORMAT_MAP.get(body.response_format, "mp3")

    # Маппинг модели
    model = body.model.lower()
    if model.startswith("fish:"):
        model = model[5:]
    fish_model_map = {"tts-1": "s1", "tts-1-hd": "s2-pro"}
    fish_model = fish_model_map.get(model, FISH_DEFAULT_MODEL)

    # Маппинг голоса
    voice = body.voice
    reference_id = FISH_VOICE_MAP.get(voice, voice)
    if reference_id in OPENAI_VOICES:
        reference_id = FISH_DEFAULT_VOICE

    fish_body: dict = {
        "text": body.input,
        "format": fish_format,
        "normalize": True,
        "temperature": 0.3,
        "top_p": 0.5,
        "condition_on_previous_chunks": True,
        "prosody": {
            "speed": max(0.5, min(2.0, body.speed)),
            "normalize_loudness": True,
        },
    }

    if reference_id:
        fish_body["reference_id"] = reference_id

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": fish_model,
    }

    logger.info(f"fish.audio request: model={fish_model}, voice={reference_id}, chars={len(body.input)}")

    req = http_client.build_request(
        "POST", f"{FISH_BASE_URL}/v1/tts", json=fish_body, headers=headers,
    )
    response = await http_client.send(req, stream=True)

    if response.status_code != 200:
        error_body = await response.aread()
        logger.error(f"fish.audio {response.status_code}: {error_body.decode(errors='replace')}")
        raise HTTPException(
            status_code=response.status_code,
            detail=f"fish.audio error: {error_body.decode(errors='replace')}",
        )

    content_type = CONTENT_TYPE_MAP.get(body.response_format, "audio/mpeg")
    return StreamingResponse(
        response.aiter_bytes(),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="speech.{fish_format}"'},
    )


# ══════════════════════════════════════════════════════════════════════════
# ElevenLabs бэкенд
# ══════════════════════════════════════════════════════════════════════════

async def synth_elevenlabs(body: OpenAISpeechRequest) -> StreamingResponse:
    api_key = get_api_key(ELEVENLABS_API_KEY, "ElevenLabs")

    # Маппинг голоса → voice_id
    voice = body.voice
    voice_id = ELEVENLABS_VOICE_MAP.get(voice, voice)
    if voice in OPENAI_VOICES and voice not in ELEVENLABS_VOICE_MAP:
        voice_id = ELEVENLABS_DEFAULT_VOICE

    # Маппинг модели
    model = body.model.lower()
    if model.startswith("elevenlabs:"):
        el_model = model[11:]
    else:
        el_model = ELEVENLABS_MODEL_ID

    output_format = ELEVENLABS_FORMAT_MAP.get(body.response_format, "mp3_44100_128")

    el_body = {
        "text": body.input,
        "model_id": el_model,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "speed": max(0.7, min(1.2, body.speed)),
        },
    }

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }

    logger.info(f"elevenlabs request: model={el_model}, voice={voice_id}, chars={len(body.input)}")

    url = f"{ELEVENLABS_BASE_URL}/v1/text-to-speech/{voice_id}/stream?output_format={output_format}"
    req = http_client.build_request("POST", url, json=el_body, headers=headers)
    response = await http_client.send(req, stream=True)

    if response.status_code != 200:
        error_body = await response.aread()
        logger.error(f"ElevenLabs {response.status_code}: {error_body.decode(errors='replace')}")
        raise HTTPException(
            status_code=response.status_code,
            detail=f"ElevenLabs error: {error_body.decode(errors='replace')}",
        )

    content_type = CONTENT_TYPE_MAP.get(body.response_format, "audio/mpeg")
    ext = body.response_format if body.response_format in ("mp3", "wav", "opus", "pcm") else "mp3"
    return StreamingResponse(
        response.aiter_bytes(),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="speech.{ext}"'},
    )


# ══════════════════════════════════════════════════════════════════════════
# VoiceBox бэкенд (локальный)
# ══════════════════════════════════════════════════════════════════════════

# Семафор: VoiceBox — локальная модель, обрабатывает только 1 запрос за раз
_voicebox_semaphore = asyncio.Semaphore(1)
VOICEBOX_MAX_RETRIES = 3


async def synth_voicebox(body: OpenAISpeechRequest) -> StreamingResponse:
    # Маппинг голоса → profile_id
    voice = body.voice
    profile_id = VOICEBOX_VOICE_MAP.get(voice, voice)
    if profile_id in OPENAI_VOICES:
        profile_id = VOICEBOX_DEFAULT_VOICE

    if not profile_id:
        raise HTTPException(
            status_code=400,
            detail="No VoiceBox profile_id. Set VOICEBOX_DEFAULT_VOICE in .env or pass profile_id as voice.",
        )

    # Маппинг engine из модели (voicebox:chatterbox → engine=chatterbox)
    model = body.model.lower()
    engine = VOICEBOX_ENGINE
    if model.startswith("voicebox:"):
        engine_override = model[9:]
        if engine_override in ("qwen", "luxtts", "chatterbox", "chatterbox_turbo"):
            engine = engine_override

    vb_body = {
        "profile_id": profile_id,
        "text": body.input,
        "language": VOICEBOX_LANGUAGE,
        "engine": engine,
        "model_size": VOICEBOX_MODEL_SIZE,
        "normalize": True,
    }

    logger.info(f"voicebox request: engine={engine}, voice={profile_id}, chars={len(body.input)}")

    async with _voicebox_semaphore:
        last_err = None
        for attempt in range(1, VOICEBOX_MAX_RETRIES + 1):
            try:
                req = http_client.build_request(
                    "POST",
                    f"{VOICEBOX_BASE_URL}/generate/stream",
                    json=vb_body,
                    headers={"Content-Type": "application/json"},
                )
                response = await http_client.send(req, stream=True)

                if response.status_code != 200:
                    error_body = await response.aread()
                    logger.error(f"voicebox {response.status_code}: {error_body.decode(errors='replace')}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"VoiceBox error: {error_body.decode(errors='replace')}",
                    )

                return StreamingResponse(
                    response.aiter_bytes(),
                    media_type="audio/wav",
                    headers={"Content-Disposition": 'attachment; filename="speech.wav"'},
                )
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_err = e
                logger.warning(f"voicebox attempt {attempt}/{VOICEBOX_MAX_RETRIES} failed: {e}")
                if attempt < VOICEBOX_MAX_RETRIES:
                    await asyncio.sleep(1)

        raise HTTPException(
            status_code=503,
            detail=f"VoiceBox unavailable after {VOICEBOX_MAX_RETRIES} retries: {last_err}",
        )


# ══════════════════════════════════════════════════════════════════════════
# Middleware
# ══════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({duration_ms:.0f}ms)")
    return response


# ══════════════════════════════════════════════════════════════════════════
# Роуты — все варианты путей для совместимости с Memo AI и др.
# ══════════════════════════════════════════════════════════════════════════

@app.post("/v1/audio/speech")
@app.post("/v1/audio/speech/")
@app.post("/audio/speech")
@app.post("/audio/speech/")
@app.post("/v1/v1/audio/speech")
@app.post("/v1/v1/audio/speech/")
async def create_speech(body: OpenAISpeechRequest):
    backend = get_backend(body)
    logger.info(f"TTS backend={backend} voice={body.voice} model={body.model} chars={len(body.input)}")

    if backend == "elevenlabs":
        return await synth_elevenlabs(body)
    if backend == "voicebox":
        return await synth_voicebox(body)
    return await synth_fish(body)


@app.get("/")
async def root():
    return {"status": "ok", "service": "tts-proxy", "version": "1.0.0", "backend": TTS_BACKEND}


@app.get("/v1/models")
@app.get("/v1/models/")
@app.get("/models")
@app.get("/models/")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "tts-1", "object": "model", "created": 0, "owned_by": "proxy",
             "description": f"Default backend: {TTS_BACKEND}"},
            {"id": "tts-1-hd", "object": "model", "created": 0, "owned_by": "proxy",
             "description": "fish.audio s2-pro"},
            {"id": "fish:s1", "object": "model", "created": 0, "owned_by": "fish-audio"},
            {"id": "fish:s2-pro", "object": "model", "created": 0, "owned_by": "fish-audio"},
            {"id": "elevenlabs:eleven_multilingual_v2", "object": "model", "created": 0,
             "owned_by": "elevenlabs"},
            {"id": "elevenlabs:eleven_turbo_v2_5", "object": "model", "created": 0,
             "owned_by": "elevenlabs"},
            {"id": "voicebox:qwen", "object": "model", "created": 0, "owned_by": "voicebox"},
            {"id": "voicebox:chatterbox", "object": "model", "created": 0, "owned_by": "voicebox"},
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "backend": TTS_BACKEND}


if __name__ == "__main__":
    import uvicorn

    print("=" * 55)
    print("  TTS Proxy — OpenAI-compatible")
    print(f"  Backend : {TTS_BACKEND}")
    print(f"  API     : http://localhost:{PROXY_PORT}/v1/audio/speech")
    print(f"  Docs    : {'http://localhost:' + str(PROXY_PORT) + '/docs' if DOCS_ENABLED else 'disabled (DOCS_ENABLED=true to enable)'}")
    print("")
    print("  Memo AI: Base URL → http://localhost:8000")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT)
