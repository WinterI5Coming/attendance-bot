FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY main.py .

# SQLite 데이터와 백업은 볼륨으로 유지한다.
VOLUME ["/app/data", "/app/logs"]

CMD ["python", "main.py"]
