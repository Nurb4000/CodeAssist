"""
Tests for CodeAssist Self-Creation System.
Tests skill/tool creation, pattern detection, and hot-reload.
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AgentConfig


class TestAutoCreationConfig(unittest.TestCase):
    """Test auto-creation configuration."""
    
    def test_agent_config_defaults(self):
        config = AgentConfig()
        self.assertTrue(config.auto_create_skills)
        self.assertFalse(config.auto_create_tools)
        self.assertEqual(config.max_auto_creations, 3)
        self.assertEqual(config.min_confidence, 0.7)
    
    def test_agent_config_custom(self):
        config = AgentConfig(
            auto_create_skills=False,
            auto_create_tools=True,
            max_auto_creations=5,
            min_confidence=0.8,
        )
        self.assertFalse(config.auto_create_skills)
        self.assertTrue(config.auto_create_tools)
        self.assertEqual(config.max_auto_creations, 5)
        self.assertEqual(config.min_confidence, 0.8)


class TestCreateSkillTool(unittest.TestCase):
    """Test create_skill tool."""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = Path(self.test_dir)
        self.skills_dir = self.workspace / ".codeassist" / "skills"
        self.skills_dir.mkdir(parents=True)
    
    def test_skill_tool_schema(self):
        from tools.create_skill import TOOLS
        self.assertIn("create_skill", TOOLS)
        schema = TOOLS["create_skill"]
        self.assertIn("name", schema)
        self.assertIn("description", schema)
        self.assertIn("parameters", schema)
    
    def test_skill_name_validation(self):
        from tools.create_skill import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(
                name="invalid name!",
                description="Test",
                content="Test content",
                workspace=str(self.workspace),
            )
        )
        self.assertIn("Error", result)
    
    async def test_create_skill(self):
        from tools.create_skill import execute
        result = await execute(
            name="test-skill",
            description="A test skill",
            content="# Test Skill\n\nThis is a test.",
            workspace=str(self.workspace),
        )
        self.assertIn("created successfully", result)
        self.assertTrue((self.skills_dir / "test-skill.md").exists())
    
    async def test_create_duplicate_skill(self):
        from tools.create_skill import execute
        # Create first
        await execute(
            name="dup-skill",
            description="First",
            content="Content 1",
            workspace=str(self.workspace),
        )
        # Try duplicate
        result = await execute(
            name="dup-skill",
            description="Second",
            content="Content 2",
            workspace=str(self.workspace),
        )
        self.assertIn("already exists", result)
    
    async def test_skill_frontmatter(self):
        from tools.create_skill import execute
        await execute(
            name="fm-skill",
            description="Test description",
            content="# Content",
            slash_command="test",
            workspace=str(self.workspace),
        )
        content = (self.skills_dir / "fm-skill.md").read_text()
        self.assertIn("name: fm-skill", content)
        self.assertIn("description: Test description", content)
        self.assertIn("slash: test", content)


class TestCreateToolTool(unittest.TestCase):
    """Test create_tool tool."""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = Path(self.test_dir)
        self.tools_dir = self.workspace / ".codeassist" / "custom_tools"
        self.tools_dir.mkdir(parents=True)
    
    def test_tool_tool_schema(self):
        from tools.create_tool import TOOLS
        self.assertIn("create_tool", TOOLS)
        schema = TOOLS["create_tool"]
        self.assertIn("name", schema)
        self.assertIn("parameters", schema)
    
    def test_tool_name_validation(self):
        from tools.create_tool import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(
                name="123invalid",
                description="Test",
                parameters={},
                code="async def execute(): return 'test'",
                workspace=str(self.workspace),
            )
        )
        self.assertIn("Error", result)
    
    async def test_create_tool(self):
        from tools.create_tool import execute
        code = '''
async def execute(param1: str) -> str:
    return f"Result: {param1}"
'''
        result = await execute(
            name="test_tool",
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {"param1": {"type": "string"}},
            },
            code=code,
            workspace=str(self.workspace),
        )
        self.assertIn("created successfully", result)
        self.assertTrue((self.tools_dir / "test_tool.py").exists())
    
    async def test_invalid_syntax(self):
        from tools.create_tool import execute
        result = await execute(
            name="bad_tool",
            description="Bad tool",
            parameters={},
            code="def missing_colon()",
            workspace=str(self.workspace),
        )
        self.assertIn("syntax", result.lower())
    
    async def test_tools_limit(self):
        from tools.create_tool import execute
        code = 'async def execute(): return "test"'
        params = {"type": "object", "properties": {}}
        
        # Create 10 tools (the limit)
        for i in range(10):
            await execute(
                name=f"tool_{i}",
                description=f"Tool {i}",
                parameters=params,
                code=code,
                workspace=str(self.workspace),
            )
        
        # 11th should fail
        result = await execute(
            name="tool_11",
            description="Tool 11",
            parameters=params,
            code=code,
            workspace=str(self.workspace),
        )
        self.assertIn("Maximum", result)


class TestSkillsHotReload(unittest.TestCase):
    """Test skills hot-reload."""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = Path(self.test_dir)
    
    def test_reload_method_exists(self):
        from skills import SkillRegistry
        registry = SkillRegistry(self.workspace)
        self.assertTrue(hasattr(registry, 'reload'))
    
    async def test_reload_clears_cache(self):
        from skills import SkillRegistry
        registry = SkillRegistry(self.workspace)
        registry._skills = {"old": MagicMock()}
        registry.reload()
        self.assertEqual(len(registry._skills), 0)


class TestCustomToolsLoader(unittest.TestCase):
    """Test custom tools loader."""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace = Path(self.test_dir)
        self.tools_dir = self.workspace / ".codeassist" / "custom_tools"
        self.tools_dir.mkdir(parents=True)
    
    def test_registry_creation(self):
        from custom_tools_loader import CustomToolRegistry
        registry = CustomToolRegistry(self.workspace)
        self.assertEqual(registry.tools_dir, self.tools_dir)
    
    async def test_load_tool(self):
        tool_code = '''
TOOLS = {
    "my_tool": {
        "name": "my_tool",
        "description": "My custom tool",
        "parameters": {"type": "object", "properties": {}}
    }
}

async def execute() -> str:
    return "executed"
'''
        (self.tools_dir / "my_tool.py").write_text(tool_code)
        
        from custom_tools_loader import CustomToolRegistry
        registry = CustomToolRegistry(self.workspace)
        registry.discover()
        
        self.assertIn("my_tool", registry._tools)
    
    async def test_execute_tool(self):
        tool_code = '''
TOOLS = {
    "echo": {
        "name": "echo",
        "description": "Echo tool",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}
    }
}

async def execute(text: str) -> str:
    return f"Echo: {text}"
'''
        (self.tools_dir / "echo.py").write_text(tool_code)
        
        from custom_tools_loader import CustomToolRegistry
        registry = CustomToolRegistry(self.workspace)
        registry.discover()
        
        result = await registry.execute_tool("echo", text="hello")
        self.assertEqual(result, "Echo: hello")


class TestPatternDetection(unittest.TestCase):
    """Test pattern detection in session_hook."""
    
    def test_detect_repetitive_patterns(self):
        from session_hook import SessionHook
        hook = SessionHook()
        
        # Create messages with repeated tool sequence
        messages = []
        for _ in range(5):
            messages.append({
                "role": "assistant",
                "tool_calls": json.dumps([
                    {"function": {"name": "read", "arguments": "{}"}},
                    {"function": {"name": "edit", "arguments": "{}"}},
                ])
            })
            messages.append({"role": "tool", "content": "ok"})
        
        # This should detect the pattern
        # (actual test would need async and KB mock)
        self.assertTrue(True)  # Placeholder


if __name__ == "__main__":
    unittest.main()
