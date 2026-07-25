# Python 3.10+ base
FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Expose orchestrator port
EXPOSE 8000

# Railway supplies PORT at runtime; Docker Compose continues to use 8000 by default.
CMD ["sh", "-c", "uvicorn orchestrator.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
