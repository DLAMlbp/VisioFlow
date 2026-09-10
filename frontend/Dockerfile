FROM node:22.22.2-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.29.1-alpine

LABEL org.opencontainers.image.source="https://github.com/zuixi01/tuxiangshibie"

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1/health || exit 1
