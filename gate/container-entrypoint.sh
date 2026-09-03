#!/bin/sh
set -e
: "${AUTH_HOST:?variabile AUTH_HOST mancante (discovery peer auth-service fallita?)}"
: "${AUTH_PORT:?variabile AUTH_PORT mancante}"
: "${GRAYLOG_HOST:?variabile GRAYLOG_HOST mancante (discovery peer graylog fallita?)}"
: "${GRAYLOG_PORT:?variabile GRAYLOG_PORT mancante}"

envsubst '${AUTH_HOST} ${AUTH_PORT} ${GRAYLOG_HOST} ${GRAYLOG_PORT}' \
  < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

exec nginx -g 'daemon off;'
