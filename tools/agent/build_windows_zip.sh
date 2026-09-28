#!/usr/bin/env bash
# Builds the Windows download, dist/AI-Melee-Windows-x86_64.zip, on a Linux
# PC: the game cross-compiled with MinGW (tools/package_windows.sh) plus the
# Python agent (package_windows_agent.sh), exactly as the (manual-only)
# ai-melee-windows.yml workflow does. Upload the zip by hand as a GitHub
# Release, or anywhere else (README: "Making the Windows download").
#
#   tools/agent/build_windows_zip.sh               in an Ubuntu 24.04 container (podman or docker)
#   tools/agent/build_windows_zip.sh --no-container  on this machine, which must be Ubuntu/Debian
#                                                    with the packages below installed
#
# The container keeps the build on the toolchain it is tested with (Ubuntu's
# mingw-w64 with POSIX threads) and installs nothing on the host besides the
# container engine (CachyOS/Arch: sudo pacman -S podman). The build folder,
# build-win/, stays in the checkout, so later builds only recompile changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE="docker.io/library/ubuntu:24.04"
PACKAGES="ca-certificates build-essential pkg-config git ninja-build cmake zip unzip cabextract curl mingw-w64 python3 python3-numpy"

inside() {
    if [[ "${1:-}" == "--install" ]]; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -q
        # shellcheck disable=SC2086
        apt-get install -y -q --no-install-recommends ${PACKAGES}
        # aurora's C++20 code needs std::thread: only the POSIX threading model has it.
        update-alternatives --set x86_64-w64-mingw32-gcc /usr/bin/x86_64-w64-mingw32-gcc-posix
        update-alternatives --set x86_64-w64-mingw32-g++ /usr/bin/x86_64-w64-mingw32-g++-posix
        git config --global --add safe.directory '*'
    fi
    cd "${ROOT_DIR}"
    TARGET_ARCH=x86_64 tools/package_windows.sh
    tools/agent/package_windows_agent.sh
}

case "${1:-}" in
    --inside)
        inside --install
        exit 0
        ;;
    --no-container)
        inside
        ;;
    "")
        if command -v podman >/dev/null; then
            engine=(podman run --rm -v "${ROOT_DIR}:${ROOT_DIR}:Z")
        elif command -v docker >/dev/null; then
            # docker runs as root: hand the results back to this user at the end.
            engine=(docker run --rm -v "${ROOT_DIR}:${ROOT_DIR}")
        else
            echo "error: needs podman or docker (CachyOS/Arch: sudo pacman -S podman)" >&2
            exit 1
        fi
        echo "=== Building in ${IMAGE} with ${engine[0]} (the first build takes a while) ==="
        "${engine[@]}" -w "${ROOT_DIR}" "${IMAGE}" "${ROOT_DIR}/tools/agent/build_windows_zip.sh" --inside
        if [[ "${engine[0]}" == docker ]]; then
            docker run --rm -v "${ROOT_DIR}:${ROOT_DIR}" "${IMAGE}" \
                chown -R "$(id -u):$(id -g)" "${ROOT_DIR}/dist" "${ROOT_DIR}/build-win"
        fi
        ;;
    -h|--help)
        sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *)
        echo "usage: $0 [--no-container]" >&2
        exit 2
        ;;
esac

zip="${ROOT_DIR}/dist/AI-Melee-Windows-x86_64.zip"
echo
echo "Done: ${zip} ($(du -h "${zip}" | cut -f1))"
echo "Upload it as a GitHub Release (README: \"Making the Windows download\")."
