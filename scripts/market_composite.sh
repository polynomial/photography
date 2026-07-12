#!/usr/bin/env bash
# Generate market timelapse composites with random sampling
# Creates composites with different image counts and compositing modes
# Modes: lighten (brightest pixels) and difference (unique/changing pixels)

echo="echo"
umask 000
echo=""
set -x

function wait_for_load() {
  jobcount=$(jobs |grep -c .)
  # Allow up to 60 concurrent jobs, only throttle if we exceed that
  if [ $jobcount -gt 60 ] ; then
    sleep 5
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 50 ] ; then
    sleep 2
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 40 ] ; then
    sleep 1
  fi
  # Allow 10+ jobs to run concurrently without throttling
}

# Function to randomly select N files from a list
# Uses sort -R for random ordering (works on macOS/BSD)
random_select() {
  local count="$1"
  sort -R | head -n "$count"
}

SOURCE_DIR="${1:-/Volumes/photos/incoming/saturday-timelapse}"
CONVERSIONS_DIR="${SOURCE_DIR}/conversions"
COMPOSITES_DIR="${SOURCE_DIR}/composites"

if [ ! -d "$CONVERSIONS_DIR" ]; then
  echo "Error: Conversions directory not found: $CONVERSIONS_DIR"
  echo "Usage: $0 [source_directory]"
  echo "Expected structure: source_directory/conversions/"
  exit 1
fi

# Create composites directory
mkdir -p "$COMPOSITES_DIR"

# Collect all JPG files from conversions directory
echo "Collecting images from $CONVERSIONS_DIR..."
ALL_IMAGES=$(find "$CONVERSIONS_DIR" -type f \( -name "*.jpg" -o -name "*.JPG" \) | sort)

TOTAL_IMAGES=$(echo "$ALL_IMAGES" | wc -l | tr -d ' ')
echo "Found $TOTAL_IMAGES images"

if [ "$TOTAL_IMAGES" -eq 0 ]; then
  echo "Error: No JPG images found in $CONVERSIONS_DIR"
  exit 1
fi

# Generate composites for each image count and mode
for image_count in 100 200 500; do
  echo ""
  echo "Generating composites with $image_count images..."
  
  # Generate for both compositing modes
  for mode in lighten difference; do
    echo "  Mode: $mode"
    
    # Generate 10 variants for each mode
    for variant in $(seq 0 9); do
      output_file="$COMPOSITES_DIR/market_${image_count}_${mode}_${variant}.jpg"
      
      if [ -f "$output_file" ]; then
        echo "Skipping $output_file (already exists)"
        continue
      fi
      
      (
        echo "Creating $output_file..."
        
        # Randomly select N images
        selected_images=$(echo "$ALL_IMAGES" | random_select "$image_count")
        
        # Get first image as base
        base_image=$(echo "$selected_images" | head -1)
        
        # Copy first image as starting point
        $echo cp "$base_image" "$output_file"
        
        # Composite remaining images using specified mode
        remaining_images=$(echo "$selected_images" | tail -n +2)
        for img in $remaining_images; do
          if [ "$mode" = "difference" ]; then
            # Difference mode: shows where pixels differ (emphasizes movement)
            # We need to handle difference mode differently - it works best when
            # we composite each image and then normalize
            $echo magick "$output_file" "$img" -gravity center -compose difference -composite -format jpg "$output_file"
          else
            # Lighten mode: takes brightest pixel (emphasizes static background)
            $echo magick "$output_file" "$img" -gravity center -compose lighten -composite -format jpg "$output_file"
          fi
        done
        
        # For difference mode, normalize the result to make it more visible
        if [ "$mode" = "difference" ]; then
          $echo magick "$output_file" -normalize -format jpg "$output_file"
        fi
        
        echo "Completed: $output_file"
      ) &
      
      wait_for_load
    done
  done
done

# Wait for all jobs to complete
wait

echo ""
echo "Done! Generated composites:"
ls -lh "$COMPOSITES_DIR"/market_*.jpg 2>/dev/null | wc -l
echo "files total"
echo ""
echo "Breakdown:"
for count in 100 200 500; do
  for mode in lighten difference; do
    file_count=$(ls "$COMPOSITES_DIR"/market_${count}_${mode}_*.jpg 2>/dev/null | wc -l | tr -d ' ')
    echo "  ${count} images, ${mode} mode: ${file_count} files"
  done
done
