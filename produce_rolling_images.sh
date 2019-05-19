#!/usr/bin/env sh
#echo=echo
for i in `seq 0 359`; do
  file=$(seq -w $i 359 | head -1)
  echo $i
  (
    $echo cp $(ls night_auto_iso_* | head -$((10 + $i)) | tail -10 | head -1) night_${file}.jpg
    for img in $(ls night_auto_iso_* | head -$((10 + $i)) | tail -10); do
      $echo convert night_${file}.jpg $img -gravity center -compose lighten -composite -format jpg night_${file}.jpg
    done
  ) &
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 24 ] ; then
    wait
  fi
done
