# Test variants — when to use them and tradeoffs

A **variant** lets one DevPlatform test adapt itself to different
candidate profiles (e.g. React vs Angular vs Vue front-end roles)
without duplicating the entire test.

## When to use variants

Create variants when:

- The role family is the same but the tech stack differs.
- The shared skills (e.g. data structures, algorithms) should be tested
  consistently across all candidates.
- You want a single report template with role-specific sections.

## Advantages

- Reduces the number of separate tests you need to maintain.
- Decreases administrative overhead; one invite flow per family.
- Lets candidates see only the sections relevant to their role.
- Generates role-specific reports out of a single underlying test.

## Disadvantages and limitations

- A test must have at least two variants to function as a variant test.
- You cannot delete a variant if only two exist (the test would no
  longer qualify as a variant test).
- Variants without routing logic are hidden from candidates until logic
  is added.
- Reporting structure is shared across variants; you cannot diverge it
  arbitrarily.

## When to create a separate test instead

- Roles are fundamentally different (e.g. backend engineer vs designer).
- You need a different time budget per role.
- You need a different proctoring or scoring rubric per role.
