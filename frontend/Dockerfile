# CentralDSP admin UI (Next.js). Multi-stage: deps -> build -> slim runtime.
# Audit O-07 / C6: базовый образ закреплён patch-версией + digest (обновлять
# осознанно, отдельным PR — docs/DEPLOYMENT.md §13); runtime от пользователя
# node (uid 1000, есть в официальном образе), не root.
FROM node:22.23.2-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
# pnpm той же major, что в CI (pnpm/action-setup version: 9, lockfile v9); без
# pin corepack тянул бы «latest» и сборка зависела бы от дня запуска.
# --frozen-lockfile: расхождение package.json с lock = ошибка сборки, не тихий resolve.
ARG PNPM_VERSION=9.15.9
RUN corepack enable && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
 && pnpm install --frozen-lockfile

FROM node:22.23.2-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN node node_modules/next/dist/bin/next build

FROM node:22.23.2-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
# .next принадлежит node: next start пишет кэш оптимизации изображений/ISR в .next/cache.
COPY --from=build --chown=node:node /app/.next ./.next
COPY --from=build --chown=root:root /app/node_modules ./node_modules
COPY --from=build --chown=root:root /app/package.json ./package.json
COPY --from=build --chown=root:root /app/next.config.ts ./next.config.ts
USER node
EXPOSE 3000
CMD ["node", "node_modules/next/dist/bin/next", "start", "-p", "3000"]
