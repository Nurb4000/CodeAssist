---
name: test-generation
description: Generate comprehensive tests for a module or function
slash: test
---

You are generating comprehensive tests. Follow these steps:

1. Identify the target file or module to test
2. Read the file to understand the functionality
3. Check for existing tests to follow conventions
4. Generate tests covering:
   - Happy path scenarios
   - Edge cases (empty inputs, null values, boundary conditions)
   - Error cases (invalid inputs, exceptions)
   - Integration scenarios if applicable
5. Use the project's existing test framework and patterns
6. Write tests that are:
   - Independent and isolated
   - Deterministic (no flaky tests)
   - Well-named (describe the behavior being tested)
   - Self-documenting

After writing tests, run them to verify they pass.
