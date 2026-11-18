# 构建上下文 = 项目根目录（见 docker-compose.yml 的 context: .）
# ---- 构建前端 ----
FROM node:20-alpine AS fe
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 后端 ----
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app/ ./app/
COPY --from=fe /fe/dist ./static
ENV STATIC_DIR=/app/static
ENV SQLITE_PATH=/data/dsh_chat.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
