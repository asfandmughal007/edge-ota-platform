#!/usr/bin/env sh
# Build the test app at several versions and push them to a registry.
#
#   REGISTRY=localhost:5000 scripts/build-edge-app.sh
#
# 1.1.0 - 1.3.0 are healthy. 1.4.0 always fails its health check and exists to
# demonstrate rollback. Runs on the host or inside the `seed` compose service.
set -eu

REGISTRY="${REGISTRY:-localhost:5000}"
cd "$(dirname "$0")/../edge-app"

build() {
    version="$1"
    broken="$2"
    image="${REGISTRY}/my-edge-app:${version}"
    docker build --quiet \
        --build-arg "APP_VERSION=${version}" \
        --build-arg "APP_BROKEN=${broken}" \
        -t "${image}" .
    docker push --quiet "${image}"
    echo "pushed ${image}"
}

build 1.1.0 false
build 1.2.0 false
build 1.3.0 false
build 1.4.0 true
