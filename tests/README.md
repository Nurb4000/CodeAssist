# CodeAssist Testing Setup

## Quick Start

### 1. Install Test Dependencies

```bash
cd /home/ziggy/Projects/CodeAssist
source .venv/bin/activate
pip install pytest pytest-asyncio pytest-cov httpx
```

### 2. Run All Tests

```bash
pytest tests/ -v
```

### 3. Run with Coverage

```bash
pytest tests/ -v --cov=codeassist --cov-report=term-missing
```

## Test Structure

```
tests/
├── conftest.py                 # Shared fixtures (database cleanup, etc.)
├── test_config.py              # Configuration system tests
├── test_session.py             # Session CRUD operations
├── test_skills.py              # Skills discovery and execution
├── test_session_manager.py     # Session fork/export/import
├── test_agents.py              # Multi-agent permissions
├── test_tools/
│   ├── test_git.py            # Git integration tests
│   └── ...                     # Other tool tests
└── test_integration.py         # End-to-end tests (TBD)
```

## Running Specific Tests

### By File

```bash
pytest tests/test_config.py -v
pytest tests/test_session.py -v
pytest tests/test_skills.py -v
```

### By Keyword

```bash
# Run only async tests
pytest tests/ -k "async"

# Run only git tests
pytest tests/test_tools/test_git.py -v

# Run only skill tests
pytest tests/test_skills.py -v
```

### With Coverage Report

```bash
pytest tests/ --cov=codeassist --cov-report=html
# Opens htmlcov/index.html in browser
```

## Test Categories

### Unit Tests
- `test_config.py` - Config loading and validation
- `test_session.py` - Database operations
- `test_skills.py` - Skill discovery
- `test_agents.py` - Permission system
- `test_tools/test_git.py` - Git operations

### Integration Tests (Coming Soon)
- `test_integration.py` - Full workflow tests
- `test_server.py` - API endpoint tests

## Test Fixtures

Tests use these shared fixtures from `conftest.py`:

- `clean_database` - Automatically cleans DB before each test
- `test_workspace` - Temporary workspace directory
- `sample_skill_content` - Sample skill markdown
- `sample_session_messages` - Sample message data

## Adding New Tests

### Example: Test a New Tool

```python
# tests/test_tools/test_my_tool.py
import pytest
from tools.my_tool import MyTool

class TestMyTool:
    def test_schema(self):
        tool = MyTool()
        schema = tool.schema()
        assert schema["name"] == "my_tool"
    
    @pytest.mark.asyncio
    async def test_execute(self):
        tool = MyTool()
        result = await tool.execute(param="value")
        assert "success" in result.output.lower()
```

### Example: Test a New Feature

```python
# tests/test_my_feature.py
import pytest

class TestMyFeature:
    def test_basic_functionality(self):
        # Your test here
        assert True
    
    @pytest.mark.asyncio
    async def test_async_operation(self):
        # Your async test here
        pass
```

## Continuous Integration

### GitHub Actions

Add `.github/workflows/tests.yml`:

```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt pytest pytest-asyncio
      - run: pytest tests/ -v --cov
```

## Troubleshooting

### Database Locked Error

Tests clean the database automatically, but if you see locked errors:

```bash
# Clean manually
rm -f data/codeassist.db
pytest tests/ -v
```

### Import Errors

Ensure you're using the virtual environment:

```bash
source .venv/bin/activate
```

### Async Test Issues

Make sure tests are marked with `@pytest.mark.asyncio`:

```python
@pytest.mark.asyncio
async def test_async_operation():
    # Your test here
    pass
```

## Coverage Goals

Target coverage by module:
- **config.py**: 90%+
- **session.py**: 95%+
- **tools/**: 85%+
- **skills.py**: 85%+
- **agents.py**: 90%+
- **session_manager.py**: 90%+

## Next Steps

1. **Run existing tests** to verify setup
2. **Add tests for new features** as you implement them
3. **Set up CI/CD** to run tests automatically
4. **Track coverage** and improve over time
5. **Add integration tests** for full workflow validation

## Useful Commands

```bash
# Run all tests
pytest tests/ -v

# Run with verbose output
pytest tests/ -vv

# Run only failing tests
pytest tests/ --lf

# Stop on first failure
pytest tests/ -x

# Generate coverage report
pytest tests/ --cov=codeassist --cov-report=term-missing

# Run specific test class
pytest tests/test_config.py::TestConfig -v

# Run specific test method
pytest tests/test_config.py::TestConfig::test_load_default_config -v
```
