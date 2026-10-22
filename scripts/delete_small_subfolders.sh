#!/usr/bin/env bash

# Delete subfolders of the reports directory that contain fewer than 10 items.
# Adjust TARGET if the reports path changes.

TARGET="/home/sameer/Public/Shared/Work/Projects/MonkeyLM/reports"

# Ensure the target directory exists.
if [[ ! -d "$TARGET" ]]; then
  # Yellow color for warning/error
  YELLOW="\033[33m"
  NC="\033[0m"
  echo -e "${YELLOW}Target directory does not exist: $TARGET${NC}"
  exit 1
fi

# Iterate over immediate subdirectories.
find "$TARGET" -mindepth 1 -maxdepth 1 -type d | while IFS= read -r dir; do
  # Count items directly inside the subdirectory (files and subdirs, not recursive).
  count=$(find "$dir" -mindepth 1 -maxdepth 1 | wc -l)
  if (( count < 10 )); then
    # Red for deletion
    RED="\033[31m"
    NC="\033[0m"
    echo -e "${RED}Deleting '$dir' (contains $count items)${NC}"
    rm -rf "$dir"
  else
    # Green for keeping
    GREEN="\033[32m"
    NC="\033[0m"
    echo -e "${GREEN}Keeping '$dir' (contains $count items)${NC}"
  fi
done
