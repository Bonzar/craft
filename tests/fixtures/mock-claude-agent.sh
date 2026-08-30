#!/usr/bin/env bash
payload="$(cat)"
if grep -q 'Deduplicate and rank the supplied review reports' <<<"$payload"; then
  printf '%s\n' '{"result":"{\"verdict\":\"APPROVE\",\"summary\":\"Mock review is clean.\",\"findings\":[]}"}'
elif grep -q 'Synthesize the supplied reports for one plan' <<<"$payload"; then
  printf '%s\n' '{"result":"Mock critic synthesis.\n\nВердикт: блокеров нет"}'
else
  printf '%s\n' '{"result":"mock agent result"}'
fi
