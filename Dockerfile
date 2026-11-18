FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Moscow
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /app bot \
    && mkdir -p /app/data && chown -R bot:bot /app
WORKDIR /app
COPY requirements-web.txt /app/requirements-web.txt
RUN python -m pip install --disable-pip-version-check --no-cache-dir -r /app/requirements-web.txt
COPY --chown=bot:bot shiftbot /app/shiftbot
USER bot
ENTRYPOINT ["python", "-m", "shiftbot"]
