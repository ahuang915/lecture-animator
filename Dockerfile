# ---- 1. Build the web interface ------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. The app: Python + Manim + LaTeX + ffmpeg -------------------------------
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/ahuang915/lecture-animator" \
      org.opencontainers.image.description="Turn a lecture narration recording into an animated lecture video."

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        build-essential pkg-config libcairo2-dev libpango1.0-dev \
        texlive-latex-base texlive-latex-recommended texlive-latex-extra \
        texlive-fonts-recommended texlive-science cm-super dvisvgm \
        fonts-dejavu fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY few_shot/ few_shot/
COPY server/ server/
COPY --from=frontend /frontend/dist frontend/dist

# Projects live on a volume so they survive restarts and upgrades.
ENV LECTURE_ANIMATOR_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
