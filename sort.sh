#!/usr/bin/env sh
mkdir movies
mv *.mpg *.avi movies/
for type in IMG combined trail random; do
  mkdir $type
  mv ${type}_* ${type}/
done

