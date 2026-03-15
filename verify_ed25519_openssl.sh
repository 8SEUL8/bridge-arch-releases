#!/usr/bin/env bash
set -euo pipefail

PUBKEY_PEM="${1:?usage: verify_ed25519_openssl.sh <pubkey_pem> <signature_file> <payload_file>}"
SIGNATURE_FILE="${2:?usage: verify_ed25519_openssl.sh <pubkey_pem> <signature_file> <payload_file>}"
PAYLOAD_FILE="${3:?usage: verify_ed25519_openssl.sh <pubkey_pem> <signature_file> <payload_file>}"

openssl pkeyutl -verify -rawin -pubin -inkey "$PUBKEY_PEM" -sigfile "$SIGNATURE_FILE" -in "$PAYLOAD_FILE" >/dev/null
