#!/bin/zsh
set -euo pipefail

project_dir="/Users/kazuma/Documents/SNS自動投稿ツール"
target_dir="${project_dir}/deliverables/chatgpt_ad_analysis"
log_dir="${HOME}/Library/Logs"
log_file="${log_dir}/cleanup-old-chatgpt-ad-analysis.log"

mkdir -p "${log_dir}"

if [[ ! -d "${target_dir}" ]]; then
  print -- "$(date '+%Y-%m-%d %H:%M:%S') Target folder not found: ${target_dir}" >> "${log_file}"
  exit 0
fi

cutoff_epoch="$(date -v-7d '+%s')"
deleted_count=0

while IFS= read -r -d '' folder; do
  modified_epoch="$(stat -f '%m' "${folder}" 2>/dev/null || print -- 0)"
  if [[ "${modified_epoch}" != <-> ]]; then
    continue
  fi

  if (( modified_epoch <= cutoff_epoch )); then
    rm -rf -- "${folder}"
    deleted_count=$((deleted_count + 1))
    print -- "$(date '+%Y-%m-%d %H:%M:%S') Deleted: ${folder}" >> "${log_file}"
  fi
done < <(
  find "${target_dir}" -mindepth 1 -maxdepth 1 -type d -name 'row_*' -print0
)

print -- "$(date '+%Y-%m-%d %H:%M:%S') Done. Deleted ${deleted_count} folder(s)." >> "${log_file}"
