#!/usr/bin/env bash
echo="echo"
umask 000
echo=""
set -x

function wait_for_load() {
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 40 ] ; then
    sleep 70
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 32 ] ; then
    sleep 30
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 32 ] ; then
    sleep 8
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 24 ] ; then
    sleep 1
  fi
}

prefix="combined_$(date +%s)"
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
$echo cp $(\ls ${prefix}_* | head -1) ${prefix}.jpg
(
  for i in ${prefix}_*; do
    $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
  done
  mv ${prefix}.jpg all_${prefix}.jpg
) &
number_to_generate=40
number_to_random=20
source_prefix=${prefix}
for n in `seq 0 ${number_to_generate}`; do 
  ( 
    cp $(ls ${source_prefix}_* | random ${number_to_random} | head -1) random_${n}_${source_prefix}.jpg
    for i in $(ls ${source_prefix}_* | random ${number_to_random}); do
      echo -n .
      $echo convert random_${n}_${source_prefix}.jpg $i -gravity center -compose lighten -composite -format jpg random_${n}_${source_prefix}.jpg
    done
  ) &
  wait_for_load
done
tprefix="trail_$(date +%s)"
source_prefix="${prefix}"
count=$(\ls -1 ${source_prefix}_* | grep -c .)
length=10
for i in `seq 0 ${count}`; do
  file=$(seq -w $i ${count} | head -1)
  echo $i
  (
    $echo cp $(ls ${source_prefix}_* | head -$(($length + $i)) | tail -$length | head -1) ${tprefix}_${file}.jpg
    for img in $(ls ${source_prefix}_* | head -$(($length + $i)) | tail -$length); do
      $echo convert ${tprefix}_${file}.jpg $img -gravity center -compose lighten -composite -format jpg ${tprefix}_${file}.jpg
    done
  ) &
  wait_for_load
done
wait
number_to_generate=20
number_to_random=10
source_prefix=${tprefix}
for n in `seq 0 ${number_to_generate}`; do 
  ( 
    cp $(ls ${source_prefix}_* | random ${number_to_random} | head -1) random_${n}_${source_prefix}.jpg
    for i in $(ls ${source_prefix}_* | random ${number_to_random}); do
      echo -n .
      $echo convert random_${n}_${source_prefix}.jpg $i -gravity center -compose lighten -composite -format jpg random_${n}_${source_prefix}.jpg
    done
  ) &
  wait_for_load
done
wait
$echo mencoder "mf://${tprefix}_*" -o ${tprefix}.avi -ovc lavc -lavcopts vcodec=mjpeg &
$echo mencoder "mf://${tprefix}_*" -o ${tprefix}.mpg -ovc copy -oac copy &
$echo mencoder "mf://IMG_*.JPG" -o IMG_${prefix}.avi -ovc lavc -lavcopts vcodec=mjpeg &
#$echo mencoder "mf://IMG_*.JPG" -o IMG_${prefix}.mpg -ovc copy -oac copy &
$echo mencoder "mf://${prefix}_*" -o ${prefix}.avi -ovc lavc -lavcopts vcodec=mjpeg &
$echo mencoder "mf://${prefix}_*" -o ${prefix}.mpg -ovc copy -oac copy &
#ffmpeg -i combined_1566777546.mpg -c:v libx264 -c:a libfaac -crf 20 -preset:v veryslow combined_1566777546.mp4
# starting at IMG_1426
# crf is the outstanding thing now 
#ffmpeg -start_number 1426 -i IMG_%04d.JPG -c:v libx264 -crf 10-25? -preset:v veryslow IMG-test.mp4

wait
echo "done:"
ls -l ${prefix}_*.jpg -l ${prefix}.* ${tprefix}.*


