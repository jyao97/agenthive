FROM nginx:alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy static placeholder (will be replaced by Vite build in Phase 3)
COPY index.html /usr/share/nginx/html/index.html

EXPOSE 80
