FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 KC_STANDALONE=1 API_UPSTREAM=http://api:8000
COPY package.json package-lock.json /app/
COPY apps/web/package.json /app/apps/web/package.json
COPY packages/api-client/package.json /app/packages/api-client/package.json
COPY packages/evidence-schema/package.json /app/packages/evidence-schema/package.json
RUN npm ci
COPY apps/web /app/apps/web
COPY packages /app/packages
RUN npm run build

FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/apps/web/.next/standalone /app/
COPY --from=build --chown=node:node /app/apps/web/.next/static /app/apps/web/.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "apps/web/server.js"]
