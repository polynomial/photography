#!/usr/bin/env sh
prefix=$1
cp $(ls ${prefix}_*.[Jj][Pp][Gg] | head -1) ${prefix}.jpg
for i in ${prefix}_*.[Jj][Pp][Gg] ; do   
  $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
done

