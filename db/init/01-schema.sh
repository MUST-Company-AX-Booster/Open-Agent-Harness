#!/bin/bash
# Runs once, on the first start of an empty data/postgres. To re-run:
# docker compose down && rm -rf data/postgres && docker compose up -d postgres
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v collector_pw="$COLLECTOR_DB_PASSWORD" \
  -v hermes_pw="$HERMES_DB_PASSWORD" \
  -v tz="$TEAM_TIMEZONE" <<'SQL'
create extension if not exists pgroonga;

create table tg_messages (
    chat_id     bigint      not null,
    message_id  bigint      not null,
    chat_title  text,
    sender_id   bigint,
    sender_name text,
    text        text        not null,
    reply_to    bigint,
    ts          timestamptz not null,
    search_text text generated always as (coalesce(sender_name, '') || ' ' || text) stored,
    primary key (chat_id, message_id)
);
create index tg_messages_chat_ts on tg_messages (chat_id, ts desc);
create index tg_messages_ts      on tg_messages (ts desc);
create index tg_messages_search  on tg_messages using pgroonga (search_text);

create table tg_sync_state (
    chat_id         bigint      primary key,
    last_message_id bigint      not null default 0,
    updated_at      timestamptz not null default now()
);

create table slack_messages (
    channel_id   text        not null,
    message_ts   text        not null,
    channel_name text,
    sender_id    text,
    sender_name  text,
    text         text        not null,
    thread_ts    text,
    ts           timestamptz not null,
    search_text  text generated always as (coalesce(sender_name, '') || ' ' || text) stored,
    primary key (channel_id, message_ts)
);
create index slack_messages_chan_ts on slack_messages (channel_id, ts desc);
create index slack_messages_ts      on slack_messages (ts desc);
create index slack_messages_search  on slack_messages using pgroonga (search_text);

-- backfilled_to: how far back we have read. last_ts: newest message seen (polling watermark).
create table slack_sync_state (
    channel_id    text        primary key,
    channel_name  text,
    backfilled_to timestamptz,
    last_ts       text,
    updated_at    timestamptz not null default now()
);

-- One summary per channel per day, written by the nightly Hermes cron job.
create table summaries (
    source       text        not null check (source in ('slack', 'telegram')),
    channel_id   text        not null,
    channel_name text,
    day          date        not null,
    summary      text        not null,
    created_at   timestamptz not null default now(),
    primary key (source, channel_id, day)
);

create role collector login password :'collector_pw';
create role hermes    login password :'hermes_pw';

grant connect on database pm to collector, hermes;
grant usage on schema public to collector, hermes;
grant select, insert, update, delete
    on tg_messages, tg_sync_state, slack_messages, slack_sync_state to collector;
grant select on all tables in schema public to hermes;
grant insert, update on summaries to hermes;
alter role hermes set statement_timeout = '30s';

alter database pm set timezone to :'tz';
SQL
