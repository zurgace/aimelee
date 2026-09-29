#!/usr/bin/env bash
# Turns upstream's Windows package (tools/package_windows.sh ->
# dist/Melee-Windows-x86_64.zip) into dist/AI-Melee-Windows-x86_64.zip: the
# same game files plus the Python agent in ai-melee/, a double-click launcher
# and a short readme. No weights, disc images or Phillip files go in: Phillip
# is downloaded by the player and the weights are exported on first run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
BASE_ZIP="${1:-${DIST_DIR}/Melee-Windows-x86_64.zip}"
OUT_ZIP="${DIST_DIR}/AI-Melee-Windows-x86_64.zip"
STAGE="${DIST_DIR}/ai-melee-windows-x86_64"

if [[ ! -f "${BASE_ZIP}" ]]; then
    echo "error: ${BASE_ZIP} not found; run tools/package_windows.sh first" >&2
    exit 1
fi

rm -rf "${STAGE}" "${OUT_ZIP}"
mkdir -p "${STAGE}"
(cd "${STAGE}" && unzip -q "${BASE_ZIP}")
# Upstream wraps everything in melee-windows-x86_64/; ours is AI-Melee/, the
# folder the README tells people to put Phillip into.
shopt -s nullglob
top=("${STAGE}"/*)
ROOT_IN_ZIP="${STAGE}/AI-Melee"
if [[ ${#top[@]} -eq 1 && -d "${top[0]}" ]]; then
    mv "${top[0]}" "${ROOT_IN_ZIP}"
else
    mkdir "${ROOT_IN_ZIP}"
    mv "${top[@]}" "${ROOT_IN_ZIP}/"
fi
if [[ ! -f "${ROOT_IN_ZIP}/melee.exe" ]]; then
    echo "error: no melee.exe in ${BASE_ZIP}" >&2
    exit 1
fi

mkdir -p "${ROOT_IN_ZIP}/ai-melee"
cp "${SCRIPT_DIR}"/*.py "${SCRIPT_DIR}/requirements.txt" "${SCRIPT_DIR}/slippi-requirements.txt" \
    "${ROOT_IN_ZIP}/ai-melee/"
# Windows tools want CRLF in the files people open by hand.
sed 's/$/\r/' "${SCRIPT_DIR}/windows/Play AI-Melee.bat" > "${ROOT_IN_ZIP}/Play AI-Melee.bat"
sed 's/$/\r/' "${SCRIPT_DIR}/windows/README-AI-Melee.txt" > "${ROOT_IN_ZIP}/README-AI-Melee.txt"
# The launcher window: AI-Melee.pyw opens it without a console; its icon goes beside launcher.py.
cp "${SCRIPT_DIR}/windows/AI-Melee.pyw" "${ROOT_IN_ZIP}/AI-Melee.pyw"
cp "${ROOT_DIR}/platforms/windows/melee.ico" "${ROOT_DIR}/platforms/linux/melee.png" "${ROOT_IN_ZIP}/ai-melee/"

(cd "${STAGE}" && zip -qr9 "${OUT_ZIP}" .)
echo "=== ${OUT_ZIP} ==="
unzip -l "${OUT_ZIP}" | grep -E "melee.exe|ai-melee/play.py|ai-melee/launcher.py|AI-Melee.pyw|Play AI-Melee.bat|README-AI-Melee.txt"
