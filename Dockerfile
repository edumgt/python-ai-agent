# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – build TypeScript → JavaScript
# ─────────────────────────────────────────────────────────────────────────────
FROM node:24-alpine AS builder

WORKDIR /app

# Install build deps for native modules (better-sqlite3)
RUN apk add --no-cache python3 make g++

COPY package*.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src/ ./src/

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – production image
# ─────────────────────────────────────────────────────────────────────────────
FROM node:24-alpine AS runner

WORKDIR /app

# Install runtime deps for native modules
RUN apk add --no-cache python3 make g++

# Copy only production node_modules
COPY package*.json ./
RUN npm ci --omit=dev

# Copy compiled output
COPY --from=builder /app/dist ./dist

# Copy static assets & data templates
COPY public/ ./public/
COPY prompts/ ./prompts/
COPY data/manifest.json ./data/manifest.json
COPY data/raw/ ./data/raw/

# Ensure data directory is writable (SQLite, collected data)
RUN mkdir -p data/raw/sample data/collected && chmod -R 777 data

EXPOSE 8000

ENV NODE_ENV=production \
    PORT=8000 \
    SQLITE_PATH=/app/data/app.db \
    RAW_DIR=/app/data/raw \
    OLLAMA_BASE_URL=http://ollama:11434 \
    VECTOR_STORE=sqlite

CMD ["node", "dist/src/server.js"]
