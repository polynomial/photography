#!/usr/bin/env bash
# Generate composites using img_0007.jpg as base with color-difference overlays
# Tests various methods to detect and overlay color differences (not brightness)

echo="echo"
umask 000
echo=""
set -x

function wait_for_load() {
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 40 ] ; then
    sleep 2
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 30 ] ; then
    sleep 1
  fi
}

SOURCE_DIR="${1:-/Volumes/photos/incoming/saturday-timelapse}"
CONVERSIONS_DIR="${SOURCE_DIR}/conversions"
COMPOSITES_DIR="${SOURCE_DIR}/composites"
BASE_IMAGE="$CONVERSIONS_DIR/img_0007.jpg"

if [ ! -f "$BASE_IMAGE" ]; then
  echo "Error: Base image not found: $BASE_IMAGE"
  exit 1
fi

if [ ! -d "$CONVERSIONS_DIR" ]; then
  echo "Error: Conversions directory not found: $CONVERSIONS_DIR"
  exit 1
fi

mkdir -p "$COMPOSITES_DIR"

# Collect sample images (use random selection from different parts of the sequence)
echo "Collecting sample images..."
ALL_IMAGES=$(find "$CONVERSIONS_DIR" -type f \( -name "*.jpg" -o -name "*.JPG" \) | sort)
TOTAL_IMAGES=$(echo "$ALL_IMAGES" | wc -l | tr -d ' ')
echo "Found $TOTAL_IMAGES total images"

# Function to randomly select N images
random_select() {
  local count="$1"
  sort -R | head -n "$count"
}

# Test with different image counts
for image_count in 50 100 200; do
  echo ""
  echo "=== Testing with $image_count overlay images ==="
  
  # Select random images for overlay
  selected_images=$(echo "$ALL_IMAGES" | random_select "$image_count")
  
  # Method 1: Hue Difference - detects color hue changes
  (
    output="$COMPOSITES_DIR/color_test_${image_count}_hue.jpg"
    echo "Creating $output (Hue Difference)..."
    
    # Convert base to HSL, extract hue channel
    magick "$BASE_IMAGE" -colorspace HSL -separate -delete 1,2 "$COMPOSITES_DIR/temp_base_hue.mpc"
    
    # Start with base image
    cp "$BASE_IMAGE" "$output"
    
    # For each overlay image, find hue differences and composite
    for img in $selected_images; do
      # Convert overlay to HSL, extract hue
      magick "$img" -colorspace HSL -separate -delete 1,2 "$COMPOSITES_DIR/temp_overlay_hue.mpc"
      
      # Find pixels where hue differs significantly
      magick "$COMPOSITES_DIR/temp_base_hue.mpc" "$COMPOSITES_DIR/temp_overlay_hue.mpc" \
        -compose difference -composite \
        -threshold 10% \
        "$COMPOSITES_DIR/temp_hue_mask.mpc"
      
      # Use mask to composite only color-different pixels
      magick "$output" "$img" "$COMPOSITES_DIR/temp_hue_mask.mpc" \
        -compose over -composite \
        "$output"
    done
    
    rm -f "$COMPOSITES_DIR"/temp_*_hue.mpc "$COMPOSITES_DIR"/temp_hue_mask.mpc
    echo "Completed: $output"
  ) &
  wait_for_load
  
  # Method 2: Chroma Difference - detects colorfulness/saturation changes
  (
    output="$COMPOSITES_DIR/color_test_${image_count}_chroma.jpg"
    echo "Creating $output (Chroma/Saturation Difference)..."
    
    # Convert base to LAB colorspace, extract chroma (a and b channels)
    magick "$BASE_IMAGE" -colorspace LAB -separate "$COMPOSITES_DIR/temp_base_lab.mpc"
    magick "$COMPOSITES_DIR/temp_base_lab.mpc[1]" "$COMPOSITES_DIR/temp_base_lab.mpc[2]" \
      -compose multiply -composite "$COMPOSITES_DIR/temp_base_chroma.mpc"
    
    cp "$BASE_IMAGE" "$output"
    
    for img in $selected_images; do
      magick "$img" -colorspace LAB -separate "$COMPOSITES_DIR/temp_overlay_lab.mpc"
      magick "$COMPOSITES_DIR/temp_overlay_lab.mpc[1]" "$COMPOSITES_DIR/temp_overlay_lab.mpc[2]" \
        -compose multiply -composite "$COMPOSITES_DIR/temp_overlay_chroma.mpc"
      
      # Find chroma differences
      magick "$COMPOSITES_DIR/temp_base_chroma.mpc" "$COMPOSITES_DIR/temp_overlay_chroma.mpc" \
        -compose difference -composite \
        -threshold 15% \
        "$COMPOSITES_DIR/temp_chroma_mask.mpc"
      
      magick "$output" "$img" "$COMPOSITES_DIR/temp_chroma_mask.mpc" \
        -compose over -composite \
        "$output"
    done
    
    rm -f "$COMPOSITES_DIR"/temp_*_lab.mpc "$COMPOSITES_DIR"/temp_*_chroma.mpc "$COMPOSITES_DIR"/temp_chroma_mask.mpc
    echo "Completed: $output"
  ) &
  wait_for_load
  
  # Method 3: Color Difference (RGB difference, ignoring brightness)
  (
    output="$COMPOSITES_DIR/color_test_${image_count}_rgb_diff.jpg"
    echo "Creating $output (RGB Color Difference)..."
    
    cp "$BASE_IMAGE" "$output"
    
    for img in $selected_images; do
      # Normalize both images to same brightness, then find color differences
      magick "$BASE_IMAGE" "$img" \
        \( -clone 0 -normalize \) \
        \( -clone 1 -normalize \) \
        -delete 0,1 \
        -compose difference -composite \
        -threshold 20% \
        "$COMPOSITES_DIR/temp_rgb_mask.mpc"
      
      magick "$output" "$img" "$COMPOSITES_DIR/temp_rgb_mask.mpc" \
        -compose over -composite \
        "$output"
    done
    
    rm -f "$COMPOSITES_DIR"/temp_rgb_mask.mpc
    echo "Completed: $output"
  ) &
  wait_for_load
  
  # Method 4: HSV Saturation Difference
  (
    output="$COMPOSITES_DIR/color_test_${image_count}_saturation.jpg"
    echo "Creating $output (Saturation Difference)..."
    
    # Extract saturation channel from base
    magick "$BASE_IMAGE" -colorspace HSV -separate -delete 0,2 "$COMPOSITES_DIR/temp_base_sat.mpc"
    
    cp "$BASE_IMAGE" "$output"
    
    for img in $selected_images; do
      magick "$img" -colorspace HSV -separate -delete 0,2 "$COMPOSITES_DIR/temp_overlay_sat.mpc"
      
      magick "$COMPOSITES_DIR/temp_base_sat.mpc" "$COMPOSITES_DIR/temp_overlay_sat.mpc" \
        -compose difference -composite \
        -threshold 25% \
        "$COMPOSITES_DIR/temp_sat_mask.mpc"
      
      magick "$output" "$img" "$COMPOSITES_DIR/temp_sat_mask.mpc" \
        -compose over -composite \
        "$output"
    done
    
    rm -f "$COMPOSITES_DIR"/temp_*_sat.mpc "$COMPOSITES_DIR"/temp_sat_mask.mpc
    echo "Completed: $output"
  ) &
  wait_for_load
  
  # Method 5: Combined Hue + Saturation (most comprehensive color difference)
  (
    output="$COMPOSITES_DIR/color_test_${image_count}_hue_sat.jpg"
    echo "Creating $output (Hue + Saturation Combined)..."
    
    cp "$BASE_IMAGE" "$output"
    
    for img in $selected_images; do
      # Convert both to HSV
      magick "$BASE_IMAGE" -colorspace HSV -separate "$COMPOSITES_DIR/temp_base_hsv.mpc"
      magick "$img" -colorspace HSV -separate "$COMPOSITES_DIR/temp_overlay_hsv.mpc"
      
      # Compare hue and saturation separately
      magick "$COMPOSITES_DIR/temp_base_hsv.mpc[0]" "$COMPOSITES_DIR/temp_overlay_hsv.mpc[0]" \
        -compose difference -composite "$COMPOSITES_DIR/temp_hue_diff.mpc"
      magick "$COMPOSITES_DIR/temp_base_hsv.mpc[1]" "$COMPOSITES_DIR/temp_overlay_hsv.mpc[1]" \
        -compose difference -composite "$COMPOSITES_DIR/temp_sat_diff.mpc"
      
      # Combine hue and saturation differences
      magick "$COMPOSITES_DIR/temp_hue_diff.mpc" "$COMPOSITES_DIR/temp_sat_diff.mpc" \
        -compose plus -composite \
        -threshold 30% \
        "$COMPOSITES_DIR/temp_combined_mask.mpc"
      
      magick "$output" "$img" "$COMPOSITES_DIR/temp_combined_mask.mpc" \
        -compose over -composite \
        "$output"
    done
    
    rm -f "$COMPOSITES_DIR"/temp_*_hsv.mpc "$COMPOSITES_DIR"/temp_*_diff.mpc "$COMPOSITES_DIR"/temp_combined_mask.mpc
    echo "Completed: $output"
  ) &
  wait_for_load
  
  # Method 6: Darken mode on color-different pixels only
  (
    output="$COMPOSITES_DIR/color_test_${image_count}_darken_color.jpg"
    echo "Creating $output (Darken Color-Different Pixels)..."
    
    cp "$BASE_IMAGE" "$output"
    
    for img in $selected_images; do
      # Find color differences (normalized RGB)
      magick "$BASE_IMAGE" "$img" \
        \( -clone 0 -normalize \) \
        \( -clone 1 -normalize \) \
        -delete 0,1 \
        -compose difference -composite \
        -threshold 15% \
        "$COMPOSITES_DIR/temp_color_mask.mpc"
      
      # Use darken mode only on color-different areas
      magick "$output" "$img" "$COMPOSITES_DIR/temp_color_mask.mpc" \
        -compose darken -composite \
        "$output"
    done
    
    rm -f "$COMPOSITES_DIR"/temp_color_mask.mpc
    echo "Completed: $output"
  ) &
  wait_for_load
done

wait

echo ""
echo "=== All color-difference composites complete! ==="
ls -lh "$COMPOSITES_DIR"/color_test_*.jpg 2>/dev/null | awk '{print $5 " - " $9}'



