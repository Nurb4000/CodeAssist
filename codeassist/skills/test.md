---
name: test
description: Write unit tests, integration tests, or test suites for code
slash: test
---

# Test Writing Skill

Create comprehensive tests for the specified code.

## Steps

1. **Analyze the code** - Understand what needs to be tested
2. **Identify test cases:**
   - Happy path scenarios
   - Edge cases
   - Error conditions
   - Boundary values
3. **Write tests** following the project's test framework
4. **Run tests** to verify they pass

## Test Structure

```python
def test_function_name_scenario():
    # Arrange
    input_data = ...
    
    # Act
    result = function_under_test(input_data)
    
    # Assert
    assert result == expected
```

## Test Categories

- **Unit Tests**: Test individual functions/methods
- **Integration Tests**: Test component interactions
- **Edge Cases**: Empty inputs, null values, max sizes
- **Error Cases**: Invalid inputs, exceptions

## Best Tests

- One assertion per test (when possible)
- Descriptive test names
- Independent tests (no shared state)
- Fast execution
