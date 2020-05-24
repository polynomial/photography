for i in *                                         
do
take_date=$(exiftool $i |grep 'Create Date' | head -1 | sed -e 's/.*: //'  -e 's/ /_/g')
if [ -n "$take_date" ]; then
mv "$i" "${take_date}.$i" &
fi
done

