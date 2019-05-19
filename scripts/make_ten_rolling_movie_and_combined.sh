#!/usr/bin/env bash
#echo="echo"
umask 000
echo=""
set -x
prefix="night_auto_iso"
for b in `seq 0 9`; do
  for n in `seq 0 9`; do
    for y in `seq 0 9`; do
      (
      echo $n $y
      $echo cp $(\ls IMG_${b}${n}${y}* | head -1) ${prefix}_${b}_${n}_${y}.jpg 
      for i in IMG_${b}${n}${y}* ; do
        echo $i
        $echo convert ${prefix}_${b}_${n}_${y}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}_${b}_${n}_${y}.jpg 
      done
      ) &
    done
    wait
  done
done
$echo mencoder "mf://${prefix}_*" -o ${prefix}.avi -ovc lavc -lavcopts vcodec=mjpeg &
$echo mencoder "mf://${prefix}_*" -o ${prefix}.mpg -ovc copy -oac copy &
$echo cp $(\ls IMG_* | head -1) ${prefix}.jpg
for i in ${prefix}_*; do
  $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
done
wait
echo "done:"
ls ${prefix}_*.jpg
ls -l ${prefix}.avi ${prefix}.jpg
