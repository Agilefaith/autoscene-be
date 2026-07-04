FROM python:3.12-slim

# FFmpeg (Debian build includes libass → subtitle burn-in works) plus fonts +
# fontconfig so the `subtitles=` filter can resolve FontName=Arial/Georgia/etc.
# (Liberation is metric-compatible with Arial; fontconfig aliases it.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fontconfig \
    fonts-liberation \
    fonts-dejavu-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
