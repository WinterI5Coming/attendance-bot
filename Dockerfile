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

# 스케줄러가 매분 갱신하는 하트비트 파일이 3분 이상 멈추면 비정상으로 본다.
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os,sys,time; p='/app/data/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 180 else 1)"

STOPSIGNAL SIGTERM

CMD ["python", "main.py"]
