FROM python:3.11

# Node.js (>=18 요구 사항 충족) 및 필요 도구 설치
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm \
  && rm -rf /var/lib/apt/lists/*

# uv 공식 이미지에서 uv 복사
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# 캐시 활용을 위해 의존성 설명 파일을 먼저 복사
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# 의존성 설치 (Node + Python)
RUN npm ci \
  && npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# 프로젝트 소스 코드 복사
COPY . .

EXPOSE 3000 5001

# 프론트엔드와 백엔드 동시 시작 (개발 모드)
CMD ["npm", "run", "dev"]