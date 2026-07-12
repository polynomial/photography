#!/usr/bin/env bash
# Convert CR3 files to JPG for faster iteration
# Preserves original CR3 files
# Outputs to conversions/ directory with timestamp-ordered sequential naming

set -e

SOURCE_DIR="${1:-/Volumes/photos/incoming/saturday-timelapse}"
CONVERSIONS_DIR="${SOURCE_DIR}/conversions"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "Error: Source directory not found: $SOURCE_DIR"
  exit 1
fi

echo "Converting CR3 files from $SOURCE_DIR to $CONVERSIONS_DIR"
echo "Original CR3 files will be preserved"
echo "Files will be named sequentially by timestamp"

# Create output directory
mkdir -p "$CONVERSIONS_DIR"

# Function to manage concurrent jobs
function wait_for_load() {
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 50 ] ; then
    sleep 3
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 40 ] ; then
    sleep 2
  fi
  jobcount=$(jobs |grep -c .)
  if [ $jobcount -gt 30 ] ; then
    sleep 1
  fi
}

# First, collect all CR3 files with their timestamps and sort by timestamp
echo "Collecting and sorting CR3 files by timestamp..."
TEMP_LIST=$(mktemp)

# Collect files and write timestamp|path pairs
find "$SOURCE_DIR/101" "$SOURCE_DIR/102" -maxdepth 1 -type f \( -iname "*.CR3" -o -iname "*.cr3" \) -print0 2>/dev/null | while IFS= read -r -d '' cr3; do
  if [ -f "$cr3" ]; then
    # Get modification time as seconds since epoch
    timestamp=$(stat -f "%m" "$cr3" 2>/dev/null || stat -c "%Y" "$cr3" 2>/dev/null)
    printf "%s|%s\n" "$timestamp" "$cr3"
  fi
done | sort -n -t'|' -k1 > "$TEMP_LIST"

TOTAL_FILES=$(wc -l < "$TEMP_LIST" | tr -d ' ')
echo "Found $TOTAL_FILES CR3 files to convert"

# Function to convert CR3 to JPG with sequential naming
convert_cr3() {
  local cr3_file="$1"
  local seq_num="$2"
  local jpg_file="$3"
  
  if [ ! -f "$jpg_file" ]; then
    # Use ImageMagick to convert CR3 to JPG
    # -quality 92 provides good balance between size and quality
    if magick "$cr3_file" -quality 92 "$jpg_file" 2>&1; then
      echo "Converted [$seq_num/$TOTAL_FILES]: $(basename "$cr3_file") -> $(basename "$jpg_file")"
    else
      echo "ERROR converting [$seq_num/$TOTAL_FILES]: $cr3_file" >&2
    fi
  else
    echo "Skipping (already exists): $(basename "$jpg_file")"
  fi
}

# Process files in timestamp order
seq_num=0
while IFS='|' read -r timestamp cr3_file || [ -n "$cr3_file" ]; do
  # Skip empty lines
  [ -z "$cr3_file" ] && continue
  # Verify file exists
  [ ! -f "$cr3_file" ] && continue
  
  seq_num=$((seq_num + 1))
  # Format sequence number with zero padding (e.g., 0001, 0002, ...)
  padded_num=$(printf "%04d" "$seq_num")
  jpg_file="$CONVERSIONS_DIR/img_${padded_num}.jpg"
  
  (
    convert_cr3 "$cr3_file" "$seq_num" "$jpg_file"
  ) &
  
  wait_for_load
done < "$TEMP_LIST"

# Clean up temp file
rm "$TEMP_LIST"

# Wait for all jobs to complete
wait

echo ""
echo "Conversion complete!"
echo "JPG files saved to: $CONVERSIONS_DIR"
echo "Total files converted: $seq_num"
echo "Original CR3 files preserved in: $SOURCE_DIR"
