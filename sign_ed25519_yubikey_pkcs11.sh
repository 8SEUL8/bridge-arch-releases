#!/usr/bin/env bash
set -euo pipefail

PAYLOAD_FILE="${1:?usage: sign_ed25519_yubikey_pkcs11.sh <payload_file> <signature_file>}"
SIGNATURE_FILE="${2:?usage: sign_ed25519_yubikey_pkcs11.sh <payload_file> <signature_file>}"

MODULE="${BRIDGE_PKCS11_MODULE:?set BRIDGE_PKCS11_MODULE to your libykcs11 path}"
KEY_ID="${BRIDGE_PKCS11_KEY_ID:?set BRIDGE_PKCS11_KEY_ID to your PIV 9c key id (for example 02)}"
MECHANISM="${BRIDGE_PKCS11_MECHANISM:-EDDSA}"

EXTRA_ARGS=()
if [[ -n "${BRIDGE_PKCS11_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=(${BRIDGE_PKCS11_EXTRA_ARGS})
fi

if [[ -n "${BRIDGE_PKCS11_PIN:-}" ]]; then
  pkcs11-tool     --module "$MODULE"     --login     --pin "$BRIDGE_PKCS11_PIN"     --sign     --mechanism "$MECHANISM"     --id "$KEY_ID"     --input-file "$PAYLOAD_FILE"     --output-file "$SIGNATURE_FILE"     "${EXTRA_ARGS[@]}"
else
  pkcs11-tool     --module "$MODULE"     --login     --sign     --mechanism "$MECHANISM"     --id "$KEY_ID"     --input-file "$PAYLOAD_FILE"     --output-file "$SIGNATURE_FILE"     "${EXTRA_ARGS[@]}"
fi
