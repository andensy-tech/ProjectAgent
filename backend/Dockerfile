# Build frontend first
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Backend stage
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy built frontend static files
COPY --from=frontend-builder /app/frontend/dist /app/static

# Create uploads directory
RUN mkdir -p /app/uploads

ARG BACKEND_PORT=8000
ENV BACKEND_PORT=${BACKEND_PORT}
EXPOSE ${BACKEND_PORT}

RUN pip install gunicorn==22.0.0
CMD gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 --timeout 600 --keep-alive 5 \
    --bind 0.0.0.0:${BACKEND_PORT}
