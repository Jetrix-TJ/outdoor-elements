# syntax=docker/dockerfile:1
# Stage 1 — build the React SPA
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/ ./
RUN npm run build

# Stage 2 — Python 3.12 runtime
FROM python:3.12-slim

# System deps for OpenCV (cv2) and PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt ./root-requirements.txt
COPY backend/requirements.txt ./backend-requirements.txt
RUN pip install --no-cache-dir -r root-requirements.txt -r backend-requirements.txt

# Application source
COPY backend/ ./backend/
COPY oe_takeoff/ ./oe_takeoff/
COPY oe_qto_render/ ./oe_qto_render/

# Built React SPA — served by FastAPI StaticFiles (same origin → no CORS)
COPY --from=frontend-build /frontend/dist ./frontend/dist

# Mount point for Cloud Storage FUSE (job PDFs, overlays, masks)
RUN mkdir -p backend/jobs

EXPOSE 8080

# Single uvicorn process; Cloud Run handles concurrency via min/max instances.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
