# Lecture Animator

Turn a recorded lecture narration into an animated lecture video. Upload your audio,
review the scene plan written from what you said, animate each scene (the visuals are
timed to your voice), request changes in plain language, and export one MP4.

Animations are written as [Manim](https://www.manim.community/) code by Claude
(Anthropic) or GPT (OpenAI) and rendered on your own computer. Your recording is
transcribed with ElevenLabs or OpenAI Whisper. You use your own API keys: one that can
animate (Anthropic or OpenAI) and one that can transcribe (ElevenLabs or OpenAI). A
single OpenAI key covers both.

**New user? Read [INSTRUCTIONS.md](INSTRUCTIONS.md)** — install, first video, tips,
troubleshooting.

## Quick start (Docker)

```bash
docker run -d --name lecture-animator --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v lecture-animator-data:/data \
  ghcr.io/ahuang915/lecture-animator:latest
```

Open <http://localhost:8000> and paste your API keys under **Settings**.

## How it works

1. **Upload** — one recording of the whole lecture, or one file per scene. Each file is
   transcribed with word timings (`asr.py`).
2. **Plan** — Claude splits the transcript into scenes and writes a shared visual style
   plus a visual description per scene (`planner.py`). For a single recording, the audio
   is then cut per scene by matching the plan's narration to the transcript
   (`aligner.py`, `binder.py`).
3. **Animate** — for each scene Claude writes Manim code using the scene's real sentence
   timings, so each visual appears as it is spoken (`generator.py`). The code renders
   and is muxed with the scene's audio (`renderer.py`). Feedback creates a new version;
   every version is kept.
4. **Export** — the chosen version of every scene, plus optional title and credits cards
   (`cards.py`), are joined into one MP4 (`stitcher.py`).

## Repository layout

| Path | Role |
| --- | --- |
| `server/` | FastAPI app: `main.py` (app + static frontend), `routes_*.py` (API), `serialize.py`, `deps.py` (API keys from request headers) |
| `session.py` | On-disk project storage (atomic JSON writes; one folder per project) |
| `asr.py` | Speech-to-text with word timestamps (ElevenLabs, or OpenAI Whisper) |
| `llm.py` | One call site for Claude or GPT models (OpenAI ids are `openai:`-prefixed) |
| `planner.py` | Transcript → scene plan (Claude) |
| `aligner.py`, `binder.py` | Cut one recording into per-scene audio |
| `perscene.py` | One-file-per-scene audio |
| `generator.py` | Scene prompt + Claude call → Manim code; cost accounting |
| `renderer.py` | Runs Manim, muxes audio |
| `stitcher.py`, `cards.py` | Final MP4 with optional title/credits cards |
| `few_shot/` | Example Manim scripts included in the generation prompt |
| `frontend/` | React (Vite) interface |

Projects are stored under `$LECTURE_ANIMATOR_DATA` (default `./projects`; `/data` in Docker).
API keys are never stored on the server: the browser keeps them and sends them as
`X-Anthropic-Key` / `X-ElevenLabs-Key` / `X-OpenAI-Key` headers. `ANTHROPIC_API_KEY` /
`ELEVENLABS_API_KEY` / `OPENAI_API_KEY` environment variables work as a fallback. When
several are present, Claude animates and ElevenLabs transcribes.

## Developing without Docker

Needs Python 3.11+, Node 20+, ffmpeg, and a LaTeX install (MacTeX/BasicTeX, TeX Live)
for Manim.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server.main:app --reload --port 8000

# in a second terminal
cd frontend && npm install && npm run dev     # http://127.0.0.1:5173
```

`npm run build` puts the interface in `frontend/dist/`, which the backend then serves at
<http://localhost:8000>.

## Building the image yourself

```bash
docker build -t lecture-animator .
docker run -d -p 127.0.0.1:8000:8000 -v lecture-animator-data:/data lecture-animator
```

Pushing to `main` builds and publishes a multi-architecture image (Intel/AMD and Apple
silicon) to GitHub Container Registry via `.github/workflows/docker.yml`.
