---
name: refactor
description: Refactor code to improve structure, readability, and maintainability
slash: refactor
---

# Refactoring Skill

Refactor the specified code to improve its quality while preserving behavior.

## Steps

1. **Understand the current code** - Read and analyze the target code
2. **Identify refactoring opportunities:**
   - Extract functions/methods
   - Simplify complex conditionals
   - Remove code duplication
   - Improve naming
   - Reduce nesting depth
   - Split large functions
3. **Apply refactoring** using edit tool with precise changes
4. **Verify behavior is preserved** - Ensure no functional changes

## Common Refactorings

- **Extract Function**: Pull code into a named function
- **Rename**: Improve variable/function names
- **Simplify Conditionals**: Use early returns, guard clauses
- **Remove Duplication**: DRY principle
- **Split Large Functions**: Break into smaller pieces

## Guidelines

- Make small, incremental changes
- Preserve all existing behavior
- Keep the same public interface
- Add comments only if complexity requires it
