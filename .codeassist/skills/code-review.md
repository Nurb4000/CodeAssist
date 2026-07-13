---
name: code-review
description: Review code for quality, bugs, and best practices
slash: review
---

# Code Review Skill

Perform a thorough code review of the provided code or file.

## Steps

1. **Read the code** - Examine the file(s) specified or the current context
2. **Check for issues:**
   - Bugs and logic errors
   - Security vulnerabilities
   - Performance problems
   - Code style violations
   - Missing error handling
   - Edge cases not handled
3. **Suggest improvements** with specific line references
4. **Provide summary** of findings by severity (critical, warning, info)

## Output Format

```
## Code Review Summary

### Critical Issues
- [file:line] Description

### Warnings  
- [file:line] Description

### Suggestions
- [file:line] Description

### Positive Notes
- What's done well
```
