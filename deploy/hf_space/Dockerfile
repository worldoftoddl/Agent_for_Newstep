# ExcelBrief for Newsteps — HF Space용 Dockerfile (GitHub 리포를 빌드 시 clone)
#
# 리포 루트의 Dockerfile과 동일한 2-스테이지 구성에, 소스를 COPY 대신
# GitHub clone으로 가져오는 src 스테이지만 추가한 변형이다.
# 코드가 갱신되면 Space Settings → Factory rebuild로 다시 clone된다.

FROM alpine/git AS src
RUN git clone --depth 1 https://github.com/worldoftoddl/ExcelBrief_for_Newsteps.git /src

# ── UI 빌드 ──────────────────────────────────────────────────────────────
FROM node:24-slim AS ui-builder
WORKDIR /build/ui
RUN corepack enable
COPY --from=src /src/ui/package.json /src/ui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY --from=src /src/ui/ ./
# NEXT_PUBLIC_* 는 빌드타임에 번들로 인라인된다. /api 는 상대 경로 —
# normalizeApiUrl이 window.location.origin과 결합하므로 어떤 도메인에서든 동작.
ENV NEXT_PUBLIC_API_URL=/api \
    NEXT_PUBLIC_ASSISTANT_ID=agent
RUN pnpm build

# ── 런타임 (Python + Node) ───────────────────────────────────────────────
FROM python:3.12-slim
# standalone server.js 실행에는 node 바이너리 하나면 충분 (npm 불필요).
COPY --from=node:24-slim /usr/local/bin/node /usr/local/bin/node

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --from=src --chown=user /src/requirements.lock /src/pyproject.toml ./
RUN pip install --no-cache-dir --user -r requirements.lock

COPY --from=src --chown=user /src/src/ src/
RUN pip install --no-cache-dir --user --no-deps .

COPY --from=src --chown=user /src/langgraph.json /src/start.sh ./
COPY --from=src --chown=user /src/data/workpapers/ data/workpapers/
COPY --from=ui-builder --chown=user /build/ui/.next/standalone ui/
COPY --from=ui-builder --chown=user /build/ui/.next/static ui/.next/static
COPY --from=ui-builder --chown=user /build/ui/public ui/public

# langgraph.json이 "env": "./.env"를 참조하므로 빈 파일을 둔다 —
# 실제 설정은 Space 시크릿이 환경변수로 주입한다.
RUN touch .env

ENV WORKPAPERS_DIR=/home/user/app/data/workpapers \
    MCP_TRANSPORT=http \
    MCP_HTTP_URL=https://toddl-auditpaper-mcp.hf.space/mcp \
    LANGSMITH_PROJECT=excelbrief \
    LANGGRAPH_API_URL=http://127.0.0.1:2024 \
    PORT=7860 \
    HOSTNAME=0.0.0.0

EXPOSE 7860
CMD ["bash", "start.sh"]
