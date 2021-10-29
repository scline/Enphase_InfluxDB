#!/bin/bash
# Simple script to build containers located in this repo, used to pipeline work later down the line

# Tag arm if built from RaspberryPi
ARCH=$(uname -m)
echo "ARCH: $ARCH"

case "$ARCH" in
  armv7*)   tag="arm7-"                             ;;
  arm64)    tag="arm64-"                            ;;
  x86_64)   tag=""                                  ;;
  *)        echo "UNKNOWN ARCH, EXITING"; exit      ;;
esac

echo "TAG: $tag"

# Build app
version=`cat $PWD/src/version`
docker build $PWD/src -t smcline06/enphase-influxdb:${tag}${version}
docker build $PWD/src -t smcline06/enphase-influxdb:${tag}latest

docker push smcline06/enphase-influxdb:${tag}${version}
docker push smcline06/enphase-influxdb:${tag}latest
