This repository contains tools for making time lapse/stacked photos. I mostly use these
to produce images from long exposure night time star captures.

Tools:
* ImageMagick
* Enfuse Enblend
* Hugin

References:

https://patdavid.net/2013/01/focus-stacking-macro-photos-enfuse.html
https://patdavid.net/2013/05/noise-removal-in-photos-with-median_6.html
https://www.youtube.com/watch?v=81krjG0_S2I
https://www.youtube.com/watch?v=ExQDiLaTzBA

https://milkywaymike.com/


mogrify vs convert
https://www.smashingmagazine.com/2015/06/efficient-image-resizing-with-imagemagick/


Workflow:

I use either a remote shutter control, or I use Av mode on a Canon 80D camera.
My lenses:

. 8mm Altura f/3.0
. 10-22mm Canon EF-S f/3.5 

The settings that matter most:

. Av Mode
. manual focus, use Live View to pixel peep and verify Hyperfocal length setting and then switch to MF
. spot metering (depending on the setting, i frequently have city lights in the frame and they can darken it otherwise)
. over expose by 2-3 stops
. auto iso 100-400 or sometimes 800
. JPG-L output only

I let it take images overnight and then the next morning download them into a directory and use:

. check for washed out images, usually after dawn it starts to wash out due to direct sun, I delete these
. remove any images where the cloud cover is too much/foggy
. check for blurry images due to fog/rain
. look at first few images to make sure any test images aren't in the batch
. run star_trails.sh
. checkout all_combined* for total star trail output
. if desired pair down the combined_* images to have fewer images based on the desire trail output

Open Issues:

. lots of online research seems to indicate that RAW/CR2 images are better for night photography, these don't work well with the tools in these scripts and are huge
. figure out how to get it to over-expose at night but not the next morning for stars and sunrise
