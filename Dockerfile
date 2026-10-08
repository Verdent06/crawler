# Optional portable image (Render uses the native Python runtime + render.yaml).
FROM node:22.14.0-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12.10-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
RUN chmod +x scripts/start.sh scripts/build.sh
COPY --from=frontend /frontend/dist ./frontend/dist
ENV PORT=8000
EXPOSE 8000
CMD ["./scripts/start.sh"]
