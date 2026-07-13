---
name: migrate
description: Help with code migrations, framework upgrades, or API version changes
slash: migrate
---

# Migration Skill

Assist with code migrations, upgrades, and breaking changes.

## Steps

1. **Understand current state** - What version/dependency are we on?
2. **Identify changes** - What changed in the new version?
3. **Find affected code** - What files/functions need updating?
4. **Create migration plan** - Order of changes
5. **Apply changes** systematically
6. **Verify** - Run tests, check for deprecation warnings

## Common Migrations

- **Framework upgrades** (Django, Flask, React, etc.)
- **Language versions** (Python 3.9 → 3.12)
- **API version changes** (REST v1 → v2)
- **Database migrations** (schema changes)
- **Library major versions** (breaking changes)

## Migration Checklist

- [ ] Review changelog/release notes
- [ ] Identify deprecated features used
- [ ] Find breaking changes affecting codebase
- [ ] Update dependencies
- [ ] Fix compilation/type errors
- [ ] Update tests
- [ ] Remove deprecated code
- [ ] Run full test suite

## Guidelines

- Migrate in small commits
- Keep tests passing throughout
- Document migration decisions
- Handle backward compatibility when possible
- Use migration tools when available
