#!/usr/bin/env bash
echo="echo"
umask 000
echo=""
set -x
prefix="composite"
#the prepare off the card produces:
#1560298847_101CANON_IMG_0357.JPG
#$echo mencoder "mf://*.JPG" -o $(date +%s)_full.avi -ovc lavc -lavcopts vcodec=mjpeg &
#$echo mencoder "mf://*.JPG" -o $(date +%s)_full.mpg -ovc copy -oac copy &

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

iter=0
for canondir in `ls | awk -F_ '{ print $2}' | sort | uniq`; do


        (
        $echo cp $(\ls *_${canondir}_IMG_${n}${y}${z}* | head -1) ${prefix}_${canondir}_${n}_${y}_${z}.jpg 
        for i in *_${canondir}_${n}${y}${z}* ; do
          echo $i
          $echo convert ${prefix}_${canondir}_${n}_${y}_${z}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}_${canondir}_${n}_${y}_${z}.jpg
        done
        ) #&
      done
      wait
    done
  done
done
exit
$echo mencoder "mf://${prefix}_*" -o ${prefix}.avi -ovc lavc -lavcopts vcodec=mjpeg &
$echo mencoder "mf://${prefix}_*" -o ${prefix}.mpg -ovc copy -oac copy &
$echo cp $(\ls ${img_prefix}* | head -1) ${prefix}.jpg
for i in ${prefix}_*; do
  $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
done
tprefix=trails
source_prefix=composite
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
$echo mencoder "mf://${tprefix}_*" -o ${tprefix}.avi -ovc lavc -lavcopts vcodec=mjpeg &
$echo mencoder "mf://${tprefix}_*" -o ${tprefix}.mpg -ovc copy -oac copy &


wait
echo "done:"
ls -l ${prefix}_*.jpg -l ${prefix}.* ${tprefix}.*


