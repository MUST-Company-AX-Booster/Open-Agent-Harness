---
name: team-history
description: Answer questions about what the team discussed, decided, or asked in Slack and Telegram, by searching the team database (summaries first, then raw messages). Use for any question about past team conversations, decisions, owners, tasks, or who said what.
---

# Searching team history

Use the `teamdb` MCP tools (`execute_sql`) against Postgres. Messages may be in any language the team uses; PGroonga also tokenizes Korean, Japanese and Chinese.

## Tables

- `summaries(source, channel_id, channel_name, day, summary)` — one per channel per day: decisions, tasks, owners, open questions. **Read these first.**
- `slack_messages(channel_id, message_ts, channel_name, sender_name, text, thread_ts, ts)`
- `tg_messages(chat_id, message_id, chat_title, sender_name, text, reply_to, ts)`
- Both message tables have `search_text` = sender name + text, indexed with PGroonga.

## Required path (follow every time, in order)

1. **Understand.** If there is an image, read it for yourself (who wrote what, what is circled or highlighted, what is being asked), but do not describe or summarize it in the answer. Restate the real question in one line (internally). If you cannot restate it confidently (unclear topic, period, person, or which part of the image), stop and ask the user 1-2 short questions with your best guess as an option, instead of searching blindly.
2. **Search wide.** Run several searches with every language the team writes in + synonyms/related names of the topic (e.g. release OR launch OR deploy OR 배포; bug OR issue OR broken OR error). Cover `summaries` and both message tables. Add distinctive words from the image (numbers, page names, labels).
   ```sql
   select 'slack' as src, channel_id as cid, channel_name as place, sender_name, left(text,400) as text, ts, message_ts as id
   from slack_messages where search_text &@~ 'termA OR termB OR termC'
   union all
   select 'telegram', chat_id::text, chat_title, sender_name, left(text,400), ts, message_id::text
   from tg_messages where search_text &@~ 'termA OR termB OR termC'
   order by ts desc limit 40;

   select source, channel_name, day, left(summary,800) from summaries
   where summary &@~ 'termA OR termB' order by day desc limit 15;  -- if no PGroonga index on summary, use: summary ilike '%termA%' or summary ilike '%termB%'
   ```
3. **Check the newest.** Recency beats match score. For any "latest / status / current / progress" question, and for every place (channel/group) where step 2 found hits, read that place's most recent messages, then widen the keyword search to the last days:
   ```sql
   select sender_name, left(text,400), ts from slack_messages
   where channel_id = '<cid>' order by ts desc limit 25;      -- telegram: tg_messages where chat_id = <cid>
   select channel_name, sender_name, left(text,400), ts from slack_messages
   where ts >= now() - interval '5 days' and search_text &@~ 'termA OR termB' order by ts desc limit 30;
   ```
   (Same for `tg_messages` with `chat_title`.) Also read around important hits (thread / +-15 messages) to see the reply, correction or decision.
4. **Verify.** Every fact in the answer must come from a message you read in this session. Before calling anything "latest", confirm no newer message on that topic exists (step 3). If a later message corrects an earlier one, use the later one. Separate decided vs only proposed.
5. **Answer short.** Conclusion first (one bold sentence), then 2-5 short bullets in the SOUL.md format, each ending with its source as `#channel · Name · Oct 8`. Same language as the question. No image description. If something needed is not in the history, say so; never fill the gap with a guess.
6. **Flag risks.** If the screenshot or question involves sensitive or wrong public information (unreleased plans, wrong terms, private numbers, anything the team said must not be public), say so in one line and suggest the action: fix / delete / do not share. Check history for what the team decided about it.

Tips: put a person's name in the query for "what did X say" (search_text includes sender). Filter time with `ts >= now() - interval '7 days'`. The database time zone is the team's.

## Rules

- Always `limit` queries (max 100 rows).
- Never write to any table except `summaries` (the database will refuse anyway).
- In answers, cite where it came from: channel/group name, sender, date.
- If nothing is found, say so and say what you searched for. Do not guess.
- For Jira questions use the `jira` MCP tools. The Jira connector only sees the team's project; jira_get_all_projects names it. Before creating or changing an issue, show the user what you will do and wait for "yes".
