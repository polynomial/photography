#!/usr/bin/env bash
# noise removal xmp from darktable UI
for i in *.CR2                          
do
  echo darktable-cli $i $(dirname $BASH_SOURCE)/darken.xmp $(echo $i | sed 's/CR2/png/')
done

