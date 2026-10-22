-- AutoMark: fields for randomized hidden tests (anti-gaming).
-- reference_solution = trusted correct code used to compute expected outputs for
-- generated random inputs; generator = how to sample those inputs.
alter table public.assignments
  add column if not exists reference_solution text,
  add column if not exists generator jsonb;
