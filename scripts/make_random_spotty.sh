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

#prefix="combined_$(date +%s)"
prefix=$1
$echo cp $(\ls ${prefix}_* | head -1) ${prefix}.jpg
(
  for i in ${prefix}_*; do
    $echo convert ${prefix}.jpg $i -gravity center -compose lighten -composite -format jpg ${prefix}.jpg
  done
  mv ${prefix}.jpg all_${prefix}.jpg
) &
number_to_generate=$2
number_to_random=$3
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

