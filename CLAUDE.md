# Avortyx backend - notes for Claude

Read `docs/SYSTEM-MAP.md` first: what each part of the platform does, what it
reads and writes, and what must always stay true. Sir's rules are in
`BUSINESS-RULES.md`; every past change is in `CHANGES.md` (search it for any
name or rule before changing it - a rule there wins over a guess).

## Working rules

- Tests: `python manage.py test --settings=config.test_settings --noinput`.
  Every fix comes with a test that fails on the old code.
- A model change needs its migration (`makemigrations --check` runs in CI).
- An API change: `python manage.py api_paths --write` and commit
  `docs/api-paths.txt` (the portal's contract check reads it).
- Anything written with `.update()` on `CallLog` must also be written to
  `CallRecord` (the Reports copy) - `.update()` fires no signal (CH-352).
- Money: cost = what was charged; one charge per call sid; every balance
  change writes a `Transaction`.
- Data-changing management commands are dry runs unless `--apply`.
- No secrets in code. No command that restarts the server outside quiet time.
- One change = one CH id + a `CHANGES.md` entry; update `docs/SYSTEM-MAP.md`
  (and the portal's copy) when a flow, table or section changes.

## Reviewing a pull request (Avortyx Guard)

The review job (`.github/workflows/claude-review.yml`) asks for an impact
report. Judge the change against the system map: which sections it touches,
which other sections read the same data or call the same functions, whether
the guard's invariants (map section 5) still hold, which tests prove it,
whether the portal must change too, and whether it needs the server restart.
