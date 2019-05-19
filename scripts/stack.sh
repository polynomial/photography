#!/usr/bin/env sh
prefix="stack"
align_image_stack -m -a ${prefix}- IM*
enfuse --exposure-weight=0 --saturation-weight=0 --contrast-weight=1 --hard-mask --output=${prefix}.tif ${prefix}-*
for type in jpg png; do
  convert ${prefix}.tif ${prefix}.${type} &
done
wait

