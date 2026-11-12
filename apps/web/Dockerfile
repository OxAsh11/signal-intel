# Build from the repo root:  docker build -f apps/web/Dockerfile .
FROM node:24-slim AS base
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /repo

FROM base AS build
# NEXT_PUBLIC_* values are inlined at build time; Railway passes service variables as build args.
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_SITE_URL
COPY . .
RUN pnpm install --frozen-lockfile --filter "web..."
RUN pnpm --filter web build

FROM base AS run
ENV NODE_ENV=production PORT=3000
COPY --from=build /repo /repo
USER node
EXPOSE 3000
CMD ["pnpm", "--filter", "web", "start"]
