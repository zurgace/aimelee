#!/usr/bin/env bash
# Turns upstream's Windows package (tools/package_windows.sh ->
# dist/Melee-Windows-x86_64.zip) into dist/AI-Melee-Windows-x86_64.zip: the
# same game files, moved into game/, plus the Python agent in ai-melee/,
# AI-Melee.exe (the one thing to double-click: it opens the launcher window),
# a console .bat and a short readme. No weights, disc images or Phillip files go in: Phillip
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
# The game goes in game/, so the top of the folder has one obvious thing to
# double-click. The names that used to sit at the top are listed for the
# launcher, which offers to tidy them up when a new zip is extracted over an
# old folder (the old melee.exe would still be there to click).
mkdir "${STAGE}/game"
(cd "${ROOT_IN_ZIP}" && ls -A) > "${STAGE}/old-top-level.txt"
mv "${ROOT_IN_ZIP}"/* "${STAGE}/game/"
mv "${STAGE}/game" "${ROOT_IN_ZIP}/game"
sed 's/$/\r/' "${STAGE}/old-top-level.txt" > "${ROOT_IN_ZIP}/game/old-top-level.txt"
rm "${STAGE}/old-top-level.txt"

mkdir -p "${ROOT_IN_ZIP}/ai-melee"
cp "${SCRIPT_DIR}"/*.py "${SCRIPT_DIR}/requirements.txt" "${SCRIPT_DIR}/slippi-requirements.txt" \
    "${ROOT_IN_ZIP}/ai-melee/"
# Windows tools want CRLF in the files people open by hand.
sed 's/$/\r/' "${SCRIPT_DIR}/windows/Play AI-Melee.bat" > "${ROOT_IN_ZIP}/Play AI-Melee.bat"
sed 's/$/\r/' "${SCRIPT_DIR}/windows/README-AI-Melee.txt" > "${ROOT_IN_ZIP}/README-AI-Melee.txt"
# The launcher window's icons go beside launcher.py.
cp "${ROOT_DIR}/platforms/windows/melee.ico" "${ROOT_DIR}/platforms/linux/melee.png" "${ROOT_IN_ZIP}/ai-melee/"
# AI-Melee.exe: opens the launcher with the windowless Python (windows/ai_melee_exe.c).
EXE_BUILD="${STAGE}/exe-build"
mkdir -p "${EXE_BUILD}"
cp "${ROOT_DIR}/platforms/windows/melee.ico" "${SCRIPT_DIR}/windows/ai_melee_exe.rc" "${EXE_BUILD}/"
"${WINDRES:-x86_64-w64-mingw32-windres}" -I "${EXE_BUILD}" "${EXE_BUILD}/ai_melee_exe.rc" -O coff \
    -o "${EXE_BUILD}/icon.o"
"${EXE_CC:-x86_64-w64-mingw32-gcc}" -O2 -Wall -municode -mwindows -s "${SCRIPT_DIR}/windows/ai_melee_exe.c" \
    "${EXE_BUILD}/icon.o" -o "${ROOT_IN_ZIP}/AI-Melee.exe"
rm -rf "${EXE_BUILD}"

(cd "${STAGE}" && zip -qr9 "${OUT_ZIP}" .)
echo "=== ${OUT_ZIP} ==="
unzip -l "${OUT_ZIP}" | grep -E "AI-Melee.exe|game/melee.exe|ai-melee/play.py|ai-melee/launcher.py|Play AI-Melee.bat|README-AI-Melee.txt"
