---
name: refactor
description: Systematic refactoring workflow with safety checks
slash: refactor
---

# Refactor Skill

Use this skill when the user wants to refactor code. Follow these steps systematically.

## Steps

1. **Understand the target**
   - Read the file(s) to be refactored
   - Use `symbol_search` to find all definitions and references
   - Use `grep` to find all usages across the codebase
   - Ask the user clarifying questions if the scope is unclear

2. **Plan the changes**
   - List every file that needs modification
   - Describe each change precisely
   - Use `question` tool to confirm the plan with the user before proceeding

3. **Create a safety snapshot**
   - Run `git_snapshot` to auto-commit the current state
   - This provides a rollback point if the refactoring goes wrong

4. **Apply changes incrementally**
   - Make one logical change at a time
   - Use `diff_preview` before each `edit` or `write` to verify the change
   - Prefer `edit` (surgical string replacement) over `write` (full file replacement)

5. **Verify after each change**
   - Run the test suite with `test_runner`
   - If tests fail, revert the last change and try a different approach
   - Use `read` to verify the modified code looks correct

6. **Final verification**
   - Run the full test suite one more time
   - Use `diff_preview` to show a summary of all changes
   - Summarize what was changed and why

## Safety Rules

- NEVER refactor without first understanding the codebase
- ALWAYS create a git snapshot before starting
- ALWAYS verify with tests after each change
- If more than 3 tests fail, STOP and ask the user for guidance
