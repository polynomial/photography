#!/system/bin/sh:
cd /mnt/sdcard/upload
find . -type f -iname "*.mpg" -o -iname "*.mov" -o -iname "*.avi" | while read name
do
  mv $name $(echo $name | sed -e 's/^..//' -e 's/\//_/g')
done
sleep 2
exit 0
