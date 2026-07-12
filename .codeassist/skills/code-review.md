---
name: code-review
description: Perform a comprehensive code review of changed files
slash: review
---

You are performing a code review. Follow these steps:

1. Use `git status` to see what files have been changed
2. Use `git diff` to see the actual changes
3. For each changed file:
   - Read the file to understand context
   - Check for common issues:
     * Security vulnerabilities
     * Performance problems
     * Error handling gaps
     * Code style inconsistencies
     * Missing edge cases
4. Provide specific, actionable feedback
5. Suggest improvements with code examples when appropriate

Focus on:
- Correctness and logic errors
- Security implications
- Performance concerns
- Maintainability
- Test coverage gaps

Be constructive and specific in your feedback.
