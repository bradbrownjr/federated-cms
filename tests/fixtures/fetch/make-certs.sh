#!/bin/sh
# Regenerates the test-only TLS fixtures for tests/test_fetch.py.
# The keys are public test material; never trust ca.pem outside the tests.
# Usage: sh tests/fixtures/fetch/make-certs.sh   (needs OpenSSL 1.1.1+)
set -eu
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

openssl req -x509 -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout "$tmp/ca.key" -out ca.pem -days 36500 -sha256 \
  -subj "/CN=federated-cms test CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash"

openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout server.key -out "$tmp/server.csr" -subj "/CN=secure.example"

cat > "$tmp/ext.cnf" <<'EXT'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:secure.example
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
EXT

openssl x509 -req -in "$tmp/server.csr" -CA ca.pem -CAkey "$tmp/ca.key" \
  -CAcreateserial -CAserial "$tmp/ca.srl" -out server.pem -days 36500 -sha256 \
  -extfile "$tmp/ext.cnf"
