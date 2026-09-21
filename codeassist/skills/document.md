---
name: document
description: Generate or improve documentation for code, APIs, or projects
slash: doc
---

# Documentation Skill

Generate clear, helpful documentation for code.

## Steps

1. **Analyze the code** - Understand its public interface and purpose
2. **Identify documentation needs:**
   - Function/method docstrings
   - Module-level documentation
   - README updates
   - API documentation
   - Usage examples
3. **Write documentation** following the project's style
4. **Add examples** where helpful

## Docstring Format

```python
def function(param1: str, param2: int = 0) -> bool:
    """Brief description of what the function does.
    
    Args:
        param1: Description of param1
        param2: Description of param2
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: When invalid input
        
    Example:
        >>> result = function("hello", 42)
        >>> print(result)
        True
    """
```

## Documentation Checklist

- [ ] One-line summary
- [ ] Detailed description if needed
- [ ] All parameters documented
- [ ] Return value documented
- [ ] Exceptions documented
- [ ] Usage example provided

## Style Guide

- Use imperative mood ("Returns" not "It returns")
- Keep descriptions concise
- Include type information
- Write for your future self
