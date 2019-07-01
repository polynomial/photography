#!/usr/bin/env sh
cp $(ls ${prefix}_* | head -1) ${prefix}.jpg
for i in ${prefix}_*; do   
  $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
done

