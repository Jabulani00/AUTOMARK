-- AutoMark prototype schema (run once in the Supabase SQL editor).
-- Mirrors the SRS data requirements (section 6). The prototype's Python server
-- acts as the trusted worker and talks to these tables with the SECRET
-- (service-role) key, which bypasses RLS. Browser-facing RLS/auth is Dev 1's
-- platform work; policies below are a safe starting point.

-- ---- profiles (role drives access) ---------------------------------------
create table if not exists public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  role        text not null default 'student' check (role in ('teacher','student')),
  created_at  timestamptz not null default now()
);

-- ---- assignments ----------------------------------------------------------
-- rubric / tests / weights are kept as JSON to match the prototype's model.
create table if not exists public.assignments (
  id          text primary key,
  teacher_id  uuid references auth.users(id),
  title       text not null,
  brief       text not null default '',
  language    text not null default 'python',
  rubric      jsonb not null default '[]'::jsonb,   -- [{id,text,marks,patterns,test_ids}]
  tests       jsonb not null default '[]'::jsonb,   -- [{id,name,kind,stdin,expected,marks,approved}]
  weights     jsonb not null default '{"tests":60,"rubric":25,"style":15}'::jsonb,
  status      text not null default 'draft' check (status in ('draft','published')),
  created_at  timestamptz not null default now()
);

-- ---- submissions ----------------------------------------------------------
create table if not exists public.submissions (
  id            text primary key,
  assignment_id text not null references public.assignments(id) on delete cascade,
  student       text not null,
  filename      text,
  code          text not null,
  status        text not null default 'pending'
                  check (status in ('pending','running','graded','failed')),
  attempts      int  not null default 1,
  result        jsonb,        -- scores + Contract B + Contract C + feedback
  error         text,
  override      jsonb,        -- {mark, reason, at} -- original is kept in result
  created_at    timestamptz not null default now()
);

create index if not exists submissions_assignment_idx on public.submissions(assignment_id);
create index if not exists submissions_status_idx      on public.submissions(status);

-- ---- Row Level Security ---------------------------------------------------
-- The service key used by the Python server bypasses RLS, so these policies
-- only constrain browser (anon/authenticated) access. Enabling RLS with no
-- permissive policy means the browser sees nothing until proper auth is added.
alter table public.profiles    enable row level security;
alter table public.assignments enable row level security;
alter table public.submissions enable row level security;

-- Example read policies (published assignments are readable once signed in).
drop policy if exists assignments_read_published on public.assignments;
create policy assignments_read_published on public.assignments
  for select using (status = 'published');

-- Students read only their own submissions (by auth email), as a starting point.
drop policy if exists submissions_read_own on public.submissions;
create policy submissions_read_own on public.submissions
  for select using (student = auth.jwt() ->> 'email');
