---
name: convert
description: Convert code between languages, frameworks, or formats
slash: convert
---

# Code Conversion Skill

Convert code from one language, framework, or format to another.

## Steps

1. **Understand source code** - What does it do?
2. **Identify target** - What language/framework/format?
3. **Map equivalents:**
   - Syntax differences
   - Standard library equivalents
   - Framework-specific patterns
   - Idiomatic constructs in target
4. **Convert incrementally** - Small, testable chunks
5. **Verify correctness** - Behavior should match

## Common Conversions

| Source | Target | Notes |
|--------|--------|-------|
| Python | JavaScript/TypeScript | async/await, types |
| JavaScript | TypeScript | Add type annotations |
| Callbacks | Promises/async | Modern async patterns |
| Class-based | Functional | React components, etc. |
| JSON | YAML | Config files |
| REST | GraphQL | API conversion |

## Guidelines

- Preserve all functionality
- Use target language idioms
- Add proper error handling for target
- Include types when converting to TS
- Test thoroughly after conversion
