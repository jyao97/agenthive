#!/bin/bash
# Generate VAPID key pair for Web Push notifications.
# Output is .env-ready — copy the two lines into your .env file.
#
# Usage:
#   ./scripts/generate-vapid-keys.sh

set -euo pipefail

# Use the project venv if available, otherwise system python
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
else
    PYTHON="python3"
fi

$PYTHON -c "
from pywebpush import Vapid
import base64

vapid = Vapid()
vapid.generate_keys()

raw_priv = vapid.private_key.private_numbers().private_value
priv_bytes = raw_priv.to_bytes(32, byteorder='big')
priv_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b'=').decode()

pub_bytes = vapid.public_key.public_bytes(
    encoding=__import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding']).Encoding.X962,
    format=__import__('cryptography.hazmat.primitives.serialization', fromlist=['PublicFormat']).PublicFormat.UncompressedPoint,
)
pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()

print(f'VAPID_PRIVATE_KEY={priv_b64}')
print(f'VAPID_PUBLIC_KEY={pub_b64}')
"
