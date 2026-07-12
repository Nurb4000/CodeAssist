"""Tests for dynamic tool loading system."""
import pytest
from pathlib import Path

from dynamic_tools import DynamicToolLoader, create_dynamic_registry
from tools import ToolRegistry


class TestDynamicToolLoader:
    """Test dynamic tool loading."""

    @pytest.fixture
    def loader(self, tmp_path):
        """Create a DynamicToolLoader instance."""
        return DynamicToolLoader(tmp_path)

    def test_discover_tools_empty(self, loader, tmp_path):
        """Test discovering tools in empty directory."""
        tools = loader.discover_tools()
        assert len(tools) == 0

    def test_discover_tools_with_valid_tool(self, loader, tmp_path):
        """Test discovering a valid tool class."""
        from tools import Tool
        
        # Create a test tool file
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        
        test_tool_file = tools_dir / "test_dynamic.py"
        test_tool_file.write_text('''
from tools import Tool

class TestDynamicTool(Tool):
    name = "test_dynamic"
    description = "A test dynamic tool"
    parameters = {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs):
        return "test"
''')
        
        tools = loader.discover_tools()
        assert len(tools) == 1
        assert tools[0].__name__ == "TestDynamicTool"

    def test_discover_tools_skips_init(self, loader, tmp_path):
        """Test that __init__.py is skipped."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        
        # Create __init__.py (should be skipped)
        init_file = tools_dir / "__init__.py"
        init_file.write_text("")
        
        tools = loader.discover_tools()
        assert len(tools) == 0

    def test_discover_tools_skips_private(self, loader, tmp_path):
        """Test that private files (starting with _) are skipped."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        
        # Create a private tool file (should be skipped)
        private_file = tools_dir / "_private_tool.py"
        private_file.write_text('''
from tools import Tool

class PrivateTool(Tool):
    name = "private"
    description = "Private"
    parameters = {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs):
        return "test"
''')
        
        tools = loader.discover_tools()
        assert len(tools) == 0

    def test_reload_registry(self, loader, tmp_path):
        """Test reloading tools into a registry."""
        from tools import Tool
        
        # Create a test tool file
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        
        test_tool_file = tools_dir / "test_reload.py"
        test_tool_file.write_text('''
from tools import Tool

class TestReloadTool(Tool):
    name = "test_reload"
    description = "A test reload tool"
    parameters = {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs):
        return "test"
''')
        
        registry = ToolRegistry(tmp_path)
        count = loader.reload_registry(registry)
        
        assert count == 1
        assert "test_reload" in registry.list_names()

    def test_get_available_tools(self, loader, tmp_path):
        """Test getting list of available tool classes."""
        from tools import Tool
        
        # Create a test tool file
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        
        test_tool_file = tools_dir / "test_available.py"
        test_tool_file.write_text('''
from tools import Tool

class TestAvailableTool(Tool):
    name = "test_available"
    description = "A test available tool"
    parameters = {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs):
        return "test"
''')
        
        loader.discover_tools()
        available = loader.get_available_tools()
        
        assert len(available) == 1
        assert available[0]["name"] == "TestAvailableTool"


class TestCreateDynamicRegistry:
    """Test creating a dynamic registry."""

    def test_create_dynamic_registry(self, tmp_path):
        """Test creating a registry with dynamic loading."""
        from tools import Tool
        
        # Create a test tool file
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        
        test_tool_file = tools_dir / "test_dyn_reg.py"
        test_tool_file.write_text('''
from tools import Tool

class TestDynRegTool(Tool):
    name = "test_dyn_reg"
    description = "A test dynamic registry tool"
    parameters = {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs):
        return "test"
''')
        
        registry, loader = create_dynamic_registry(tmp_path)
        
        assert "test_dyn_reg" in registry.list_names()
