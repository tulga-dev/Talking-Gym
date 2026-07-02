FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Long-polling worker: no port to expose. Run exactly ONE replica
# (Telegram getUpdates conflicts if two instances poll the same token).
CMD ["python", "main.py"]
