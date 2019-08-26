#!/usr/bin/env sh
find * -type d -print | while read dir; do
  pushd $dir
    while ls -1 *.[Jj][Pp][Gg] >/dev/null 2>&1; do
      move_dir=$(date +%s.%N)
      mkdir $move_dir
      for image in $(ls -1 *.[Jj][Pp][Gg] | head -200); do
        mv $image $move_dir/
      done
    done
  popd
done
