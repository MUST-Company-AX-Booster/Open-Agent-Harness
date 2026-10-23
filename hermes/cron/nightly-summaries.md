Nightly team summary. Use the teamdb tools. execute_sql takes only a sql string (no bind parameters).
1. Find every Slack channel and Telegram group with messages yesterday:
   slack_messages where ts >= current_date - 1 and ts < current_date (group by channel_id, channel_name),
   tg_messages likewise (group by chat_id, chat_title).
2. For each one, read all of yesterday's messages in time order.
3. Write a short summary in the language the team used, with these headings:
   Decisions, Tasks (with owner), Open questions. Omit a heading entirely when it has nothing - never write "none". Max ~15 lines.
4. Save it with exactly one statement of this shape per channel (source is 'slack' or 'telegram'; channel_id as a quoted text literal). channel_name and summary MUST be written with Postgres dollar quoting exactly as shown, never with ordinary single quotes, because chat text can contain quotes or SQL:
   insert into summaries (source, channel_id, channel_name, day, summary)
   values ('slack', 'C123', $n$channel name$n$, current_date - 1, $s$...summary...$s$)
   on conflict (source, channel_id, day) do update set summary = excluded.summary, channel_name = excluded.channel_name, created_at = now();
   Never put the text $s$ or $n$ inside the name or summary. Each statement must be exactly one "insert into summaries ..." and nothing else; never run any other write.
5. Reply with one line per channel: name + number of messages summarised.
