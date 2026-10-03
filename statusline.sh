#!/bin/sh
# Claude Code status line, shared by every account on this machine:
#   [personal] basename  git:(branch) ~changes +ahead -behind  ctx:XX%  5h:XX% (Nh Nm)  7d:XX% (Nd Nh)
#
# The account prefix is derived from CLAUDE_CONFIG_DIR, which the
# claude-personal alias sets and the default account leaves unset, so one
# script serves both. settings.json in each config dir points here:
#   "statusLine": {"type": "command", "command": "sh <jeeves>/tools/podbay/statusline.sh"}
#
# Before rendering, the whole input JSON is saved as a snapshot under
# ~/.local/state/podbay/status/<session_id>.json with the account label
# added: podbay reads the context and rate-limit figures from there, since
# nothing else on the machine exposes them. The write is atomic (temp file
# and rename) because podbay reads the directory while Claude writes it.

input=$(cat)

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
account=$(basename "$config_dir")
case "$account" in
  .claude) account="claude" ;;
  .claude-*) account="${account#.claude-}" ;;
esac

# --- Snapshot for podbay --------------------------------------------------

snapshot_dir="$HOME/.local/state/podbay/status"
session_id=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)
if [ -n "$session_id" ] && mkdir -p "$snapshot_dir" 2>/dev/null; then
  tmp="$snapshot_dir/.$session_id.$$.tmp"
  if printf '%s' "$input" | jq --arg a "$account" '. + {account: $a}' > "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$snapshot_dir/$session_id.json" 2>/dev/null || rm -f "$tmp"
  else
    rm -f "$tmp"
  fi
fi

# --- Rendering ------------------------------------------------------------

# Returns an ANSI color code based on percentage thresholds.
# 0-49 -> green, 50-69 -> yellow, 70-84 -> amber, 85-100 -> red
color_for_pct() {
  pct=$(printf '%.0f' "$1")
  if [ "$pct" -ge 85 ]; then
    printf '\033[31m'        # red
  elif [ "$pct" -ge 70 ]; then
    printf '\033[38;5;214m'  # amber
  elif [ "$pct" -ge 50 ]; then
    printf '\033[33m'        # yellow
  else
    printf '\033[32m'        # green
  fi
}

# Formats seconds-until-reset as "Nd Nh" or "Nh Nm" (empty if elapsed)
fmt_reset() {
  secs=$1
  [ "$secs" -gt 0 ] || return 0
  days=$((secs / 86400))
  hours=$(((secs % 86400) / 3600))
  if [ "$days" -gt 0 ]; then
    printf '%sd %sh' "$days" "$hours"
  else
    mins=$(((secs % 3600) / 60))
    printf '%sh %sm' "$hours" "$mins"
  fi
}

# Renders one token/rate segment: dim label + colored value%
segment() {
  label="$1"
  raw="$2"
  pct=$(printf '%.0f' "$raw")
  color=$(color_for_pct "$raw")
  printf '\033[2m%s\033[0m%s%s%%\033[0m' "$label" "$color" "$pct"
}

# --- Left: account, basename of cwd, git branch ---

cwd=$(printf '%s' "$input" | jq -r '.workspace.current_dir // .cwd // empty')
base_cwd=$(basename "$cwd" 2>/dev/null)

branch=""
changes=0
ahead=0
behind=0
if [ -d "$cwd/.git" ] || git -C "$cwd" rev-parse --git-dir >/dev/null 2>&1; then
  branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null)
  if [ -z "$branch" ]; then
    branch=$(git -C "$cwd" --no-optional-locks rev-parse --short HEAD 2>/dev/null)
  fi
  changes=$(git -C "$cwd" --no-optional-locks status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  counts=$(git -C "$cwd" --no-optional-locks rev-list --left-right --count '@{u}...HEAD' 2>/dev/null)
  if [ -n "$counts" ]; then
    behind=$(echo "$counts" | awk '{print $1}')
    ahead=$(echo "$counts"  | awk '{print $2}')
  fi
fi

out=""
if [ "$account" != "claude" ]; then
  out="\033[2m[${account}]\033[0m"
fi
if [ -n "$base_cwd" ]; then
  out="${out}${out:+ }\033[1;36m${base_cwd}\033[0m"
fi
if [ -n "$branch" ]; then
  out="${out} \033[2mgit:(\033[0m\033[1;35m${branch}\033[0m\033[2m)\033[0m"
  [ "$changes" -gt 0 ] && out="${out} \033[2;33m~${changes}\033[0m"
  [ "$ahead" -gt 0 ]   && out="${out} \033[2;36m+${ahead}\033[0m"
  [ "$behind" -gt 0 ]  && out="${out} \033[2;31m-${behind}\033[0m"
fi

# --- Right-side metrics (inline, no padding) ---

ctx=$(printf '%s' "$input"        | jq -r '.context_window.used_percentage // empty')
five=$(printf '%s' "$input"       | jq -r '.rate_limits.five_hour.used_percentage // empty')
five_reset=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
week=$(printf '%s' "$input"       | jq -r '.rate_limits.seven_day.used_percentage // empty')
week_reset=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

now=$(date +%s)

if [ -n "$ctx" ]; then
  out="${out}  $(segment 'ctx:' "$ctx")"
fi
if [ -n "$five" ]; then
  out="${out}  $(segment '5h:' "$five")"
  if [ -n "$five_reset" ]; then
    reset_str=$(fmt_reset $((five_reset - now)))
    [ -n "$reset_str" ] && out="${out} \033[2m(${reset_str})\033[0m"
  fi
fi
if [ -n "$week" ]; then
  out="${out}  $(segment '7d:' "$week")"
  if [ -n "$week_reset" ]; then
    reset_str=$(fmt_reset $((week_reset - now)))
    [ -n "$reset_str" ] && out="${out} \033[2m(${reset_str})\033[0m"
  fi
fi

printf "%b" "$out"
