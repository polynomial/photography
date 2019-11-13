#!/usr/bin/env bash
#echo="echo"
umask 000
echo=""
set -x
prefix="night_auto_iso"
for b in `seq 0 9`; do
  for n in `seq 0 9`; do
    for y in `seq 0 9`; do
      for z in `seq 0 9`; do
        (
        $echo cp $(\ls IMG_${b}${n}${y}${z}* | head -1) ${prefix}_${b}_${n}_${y}_${z}.jpg 
        for i in IMG_${b}${n}${y}${z}* ; do
          echo $i
          $echo convert ${prefix}_${b}_${n}_${y}_${z}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}_${b}_${n}_${y}_${z}.jpg
        done
        ) &
      done
      wait
    done
  done
done
$echo cp $(\ls IMG_* | head -1) ${prefix}.jpg
for i in ${prefix}_*; do
  $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
done
tprefix=trail_night
source_prefix=night_auto_iso
count=$(\ls -1 ${source_prefix}_* | grep -c .)
for i in `seq 0 ${count}`; do
  file=$(seq -w $i ${count} | head -1)
  echo $i
  (
    $echo cp $(ls ${source_prefix}_* | head -$((10 + $i)) | tail -10 | head -1) ${tprefix}_${file}.jpg
    for img in $(ls ${source_prefix}_* | head -$((10 + $i)) | tail -10); do
      $echo convert ${tprefix}_${file}.jpg $img -gravity center -compose lighten -composite -format jpg ${tprefix}_${file}.jpg
    done
  ) &
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 24 ] ; then
    wait
  fi
done
wait
$echo mencoder "mf://IMG_*.JPG" -o IMG.mpg -ovc copy -oac copy &
$echo mencoder "mf://${prefix}_*" -o ${prefix}.mpg -ovc copy -oac copy &
$echo mencoder "mf://${tprefix}_*" -o ${tprefix}.mpg -ovc copy -oac copy &
wait
$echo ffmpeg -i IMG.mpg -c:v libx264 -c:a libfaac -crf 24 -preset:v veryslow IMG.mp4
$echo ffmpeg -i ${prefix}.mpg -c:v libx264 -c:a libfaac -crf 15 -preset:v veryslow ${prefix}.mp4
$echo ffmpeg -i ${tprefix}.mpg -c:v libx264 -c:a libfaac -crf 15 -preset:v veryslow ${tprefix}.mp4


wait
echo "done:"
ls -l ${prefix}_*.jpg -l ${prefix}.* ${tprefix}.*


