---
name: generate
description: Generate boilerplate code, scaffolding, or repetitive patterns
slash: generate
---

# Code Generation Skill

Generate boilerplate code, templates, or scaffolding.

## Steps

1. **Understand requirements** - What needs to be generated?
2. **Check existing patterns** - Follow project conventions
3. **Generate code** with:
   - Proper structure
   - Type hints/annotations
   - Error handling
   - Documentation
4. **Customize** for specific use case

## Common Generators

- **CRUD operations** for database models
- **API endpoints** with validation
- **Test scaffolding** with fixtures
- **Configuration classes** with defaults
- **CLI argument parsers**
- **Event handlers/listeners**
- **Data classes/types**

## Template Structure

```python
from typing import Optional
from dataclasses import dataclass


@dataclass
class Item:
    """Item description."""
    id: int
    name: str
    description: Optional[str] = None
    
    def validate(self) -> bool:
        """Validate item fields."""
        return bool(self.name)
```

## Guidelines

- Match existing code style
- Include type hints
- Add docstrings
- Follow naming conventions
- Keep it DRY
