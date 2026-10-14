# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm install

# Copy source and build
COPY . .
RUN npm run build

# Production stage - serve static files via nginx
FROM nginx:alpine
ARG FRONTEND_PORT=3000
ARG BACKEND_PORT=8000
ENV FRONTEND_PORT=${FRONTEND_PORT}
ENV BACKEND_PORT=${BACKEND_PORT}

# Copy built static files
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config for SPA routing + API proxy
RUN echo 'server { \
    listen '"$FRONTEND_PORT"'; \
    root /usr/share/nginx/html; \
    index index.html; \
    \
    # API proxy to backend \
    location /api { \
        proxy_pass http://backend:'"$BACKEND_PORT"'/api; \
        proxy_http_version 1.1; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
    } \
    \
    # Health check proxy \
    location /health { \
        proxy_pass http://backend:'"$BACKEND_PORT"'/health; \
    } \
    \
    # Serve static files \
    location /static { \
        proxy_pass http://backend:'"$BACKEND_PORT"'/static; \
    } \
    \
    # SPA fallback \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE ${FRONTEND_PORT}

CMD ["nginx", "-g", "daemon off;"]
