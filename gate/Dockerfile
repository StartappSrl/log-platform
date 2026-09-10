FROM nginx:1.27-alpine
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY login.html /usr/share/nginx/html/login.html
COPY startapp-logo.png /usr/share/nginx/html/startapp-logo.png
COPY defendia-logo.png /usr/share/nginx/html/defendia-logo.png
COPY container-entrypoint.sh /container-entrypoint.sh
RUN chmod +x /container-entrypoint.sh
ENTRYPOINT ["/container-entrypoint.sh"]
