FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -r appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY static/ static/

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
