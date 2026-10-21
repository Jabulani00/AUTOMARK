-- AutoMark prototype — application login accounts (simple server-side auth).
-- The Python server owns authentication for the prototype: it stores a salted
-- password hash here and issues a signed session token. Supabase's own GoTrue
-- Auth (auth.users) would be used in the full product; this keeps the prototype
-- self-contained. RLS is ON with no browser policy, so only the server (service
-- key) ever reads this table — password hashes never reach a browser.

create table if not exists public.app_users (
  id            text primary key,
  email         text unique not null,
  password_hash text not null,
  role          text not null default 'student' check (role in ('teacher','student')),
  display_name  text,
  created_at    timestamptz not null default now()
);

alter table public.app_users enable row level security;
-- (no permissive policy on purpose: service-role server access only)
