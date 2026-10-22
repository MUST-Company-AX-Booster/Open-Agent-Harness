#!/bin/bash
# Proves the hermes DB user can read everything, write only summaries,
# and that PGroonga finds Korean words. Run from the repo root: ./db/check_permissions.sh
set -uo pipefail
q() { docker compose exec -T postgres psql -U "$1" -d pm -tAc "$2" 2>&1; }
fail=0
expect_ok()     { out=$(q "$1" "$2"); [[ $? -eq 0 && "$out" != *ERROR* ]] && echo "ok:   $3" || { echo "FAIL: $3 -> $out"; fail=1; }; }
expect_denied() { out=$(q "$1" "$2"); [[ "$out" == *"permission denied"* ]] && echo "ok:   $3" || { echo "FAIL: $3 -> $out"; fail=1; }; }

expect_ok     collector "insert into tg_messages (chat_id, message_id, text, ts) values (-1, 1, '배포 일정 확인 부탁드립니다', now())" "collector can write tg_messages"
expect_ok     hermes    "select count(*) from tg_messages"                         "hermes can read tg_messages"
expect_denied hermes    "insert into tg_messages (chat_id, message_id, text, ts) values (-1, 2, 'x', now())" "hermes cannot insert tg_messages"
expect_denied hermes    "delete from slack_messages"                               "hermes cannot delete slack_messages"
out=$(q hermes "drop table summaries"); [[ "$out" == *"must be owner"* || "$out" == *"permission denied"* ]] && echo "ok:   hermes cannot drop tables" || { echo "FAIL: hermes cannot drop tables -> $out"; fail=1; }
expect_ok     hermes    "insert into summaries (source, channel_id, day, summary) values ('telegram', '-1', current_date, 'a') on conflict (source, channel_id, day) do update set summary = excluded.summary" "hermes can upsert summaries"
out=$(q hermes "show statement_timeout"); [[ "$out" == "30s" ]] && echo "ok:   hermes statement_timeout is 30s" || { echo "FAIL: statement_timeout -> $out"; fail=1; }
out=$(q hermes "select count(*) from tg_messages where chat_id = -1 and search_text &@~ '배포'")
[[ "$out" == "1" ]] && echo "ok:   PGroonga finds Korean word" || { echo "FAIL: Korean search -> $out"; fail=1; }

# clean up test rows
q postgres "delete from tg_messages where chat_id = -1; delete from summaries where channel_id = '-1'" >/dev/null
exit $fail
