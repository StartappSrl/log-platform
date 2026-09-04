#!/bin/sh
set -e
: "${AUTH_HOST:?variabile AUTH_HOST mancante (discovery peer auth-service fallita?)}"
: "${AUTH_PORT:?variabile AUTH_PORT mancante}"

envsubst '${AUTH_HOST} ${AUTH_PORT}' \
  < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

exec nginx -g 'daemon off;'
