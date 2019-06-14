#!/usr/bin/env sh

echo=""
rm -rf EOSMISC
DATE=$(date +%s); for i in */*.JPG
do
  $echo mv $i ${DATE}_$(echo $i | sed 's/\//_/g')
done

