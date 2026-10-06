# AlphaScalp Trading Engine V6.0 Dockerfile
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output for real-time logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose Streamlit dashboard port (8501) and API port (8550)
EXPOSE 8501
EXPOSE 8550

# Default command runs the Trading Bot
CMD ["python", "bot.py"]
