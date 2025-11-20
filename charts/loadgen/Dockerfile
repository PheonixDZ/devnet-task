FROM python:3.11-slim

# Create working directory
WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY main.py .

# Non-root user (optional but recommended)
RUN useradd -m loadgen
USER loadgen

EXPOSE 8000

ENTRYPOINT ["python", "main.py"]
