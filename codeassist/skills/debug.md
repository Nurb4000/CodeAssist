---
name: debug
description: Step-by-step debugging workflow
slash: debug
---

# Debug Skill

Use this skill when the user reports a bug or error. Follow this systematic debugging process.

## Steps

1. **Reproduce the issue**
   - Ask the user for the exact steps to reproduce
   - Use `shell` to run the failing command
   - Capture and analyze the error output
   - Note the exact error message, stack trace, and file/line numbers

2. **Isolate the cause**
   - Read the file(s) mentioned in the stack trace
   - Use `grep` to search for related error patterns
   - Use `read` to examine the failing code and its surrounding context
   - Check recent changes with `git log` and `git diff`

3. **Form a hypothesis**
   - Based on the evidence, form 1-3 hypotheses about the root cause
   - Use `question` tool to share your hypotheses with the user
   - Prioritize the most likely hypothesis

4. **Test the hypothesis**
   - Add debug logging or use `shell` to run diagnostic commands
   - Use `symbol_search` to trace data flow through the code
   - Verify the hypothesis with concrete evidence

5. **Implement the fix**
   - Use `diff_preview` to show the proposed fix before applying
   - Apply the fix with `edit` (prefer surgical changes)
   - Run `test_runner` to verify the fix works
   - Check that no existing tests were broken

6. **Verify the fix**
   - Run the original failing command to confirm it's fixed
   - Run the full test suite
   - Use `question` tool to ask the user to confirm the fix

## Debugging Tips

- Start with the error message — it usually contains the key clue
- Check the simplest explanation first (typos, missing imports, wrong paths)
- Use `grep -r "TODO\\|FIXME\\|HACK" --include="*.py"` to find known issues
- If the bug is in a dependency, check the dependency's issue tracker with `websearch`
