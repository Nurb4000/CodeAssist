---
name: lint
description: Fix linting issues, formatting problems, and style violations
slash: lint
---

# Lint & Format Skill

Fix linting issues and apply consistent code formatting.

## Steps

1. **Identify linter** - What tool is configured? (eslint, ruff, black, etc.)
2. **Run linter** to get issue list
3. **Fix issues:**
   - Auto-fixable issues (formatting, imports)
   - Manual fixes (logic, style)
4. **Verify** - Re-run linter to confirm clean

## Common Linters

| Language | Linter | Formatter |
|----------|--------|-----------|
| Python | ruff, flake8, pylint | black, ruff format |
| JavaScript | eslint | prettier |
| TypeScript | tsc, eslint | prettier |
| Go | golangci-lint | gofmt |
| Rust | clippy | rustfmt |

## Issue Categories

- **Formatting**: Indentation, spacing, line length
- **Style**: Naming, import order, quotes
- **Errors**: Unused variables, missing returns
- **Complexity**: Nested code, long functions

## Guidelines

- Configure linter to match project style
- Use auto-fix for formatting issues
- Fix errors before style issues
- Add exceptions with justification
- Keep linter config in version control
