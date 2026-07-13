---
name: debug
description: Help debug issues by analyzing code, adding diagnostics, and tracing problems
slash: debug
---

# Debugging Skill

Systematically debug an issue by analyzing code and tracing the problem.

## Steps

1. **Understand the symptoms** - What is the observed behavior vs expected?
2. **Gather context:**
   - Read relevant code files
   - Check error messages and stack traces
   - Look for recent changes
3. **Form hypotheses** - What could cause this behavior?
4. **Add diagnostics** if needed:
   - Print statements
   - Logging calls
   - Assertions
5. **Test hypotheses** systematically
6. **Fix the root cause** - Not just symptoms
7. **Verify the fix** works

## Debugging Checklist

- [ ] Reproduce the issue reliably
- [ ] Check for null/undefined values
- [ ] Verify assumptions about data types
- [ ] Look for race conditions
- [ ] Check boundary conditions
- [ ] Review error handling paths
- [ ] Verify external dependencies

## Common Patterns

- **Off-by-one errors**: Check loop bounds
- **Null references**: Add null checks
- **Async issues**: Verify promise handling
- **State mutations**: Check for unintended side effects
