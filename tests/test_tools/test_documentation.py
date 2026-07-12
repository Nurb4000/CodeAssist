"""Tests for documentation tool."""
import pytest
from pathlib import Path

from tools.documentation import DocumentationTool


class TestDocumentationTool:
    """Test documentation tool operations."""

    @pytest.fixture
    def doc_tool(self):
        """Create a DocumentationTool instance."""
        return DocumentationTool()

    @pytest.fixture
    def sample_python_file(self, tmp_path):
        """Create a sample Python file for testing."""
        py_file = tmp_path / "sample.py"
        py_file.write_text('''
"""Sample module for testing."""

class SampleClass:
    """A sample class."""
    
    def __init__(self, name: str, age: int = 0):
        """Initialize the class.
        
        Args:
            name: The name
            age: The age
        """
        self.name = name
        self.age = age
    
    def greet(self) -> str:
        """Greet the user.
        
        Returns:
            A greeting string
        """
        return f"Hello, {self.name}!"
    
    def _private_method(self):
        """A private method."""
        pass

def standalone_function(x: int, y: str = "default") -> bool:
    """A standalone function.
    
    Args:
        x: First parameter
        y: Second parameter
    
    Returns:
        True if successful
    """
    return True
''')
        return py_file

    @pytest.mark.asyncio
    async def test_extract_python_functions(self, doc_tool, sample_python_file):
        """Test extracting Python functions."""
        doc_tool.workspace = sample_python_file.parent
        
        result = await doc_tool.execute(
            action="extract",
            file_path=str(sample_python_file),
            format="text"
        )
        
        assert "standalone_function" in result.output
        assert "SampleClass" in result.output

    @pytest.mark.asyncio
    async def test_extract_with_markdown_format(self, doc_tool, sample_python_file):
        """Test extracting with Markdown format."""
        doc_tool.workspace = sample_python_file.parent
        
        result = await doc_tool.execute(
            action="extract",
            file_path=str(sample_python_file),
            format="markdown"
        )
        
        assert "# Documentation" in result.output
        assert "## Function: standalone_function" in result.output
        assert "## Class: SampleClass" in result.output

    @pytest.mark.asyncio
    async def test_exclude_private_members(self, doc_tool, sample_python_file):
        """Test excluding private members."""
        doc_tool.workspace = sample_python_file.parent
        
        result = await doc_tool.execute(
            action="extract",
            file_path=str(sample_python_file),
            format="text",
            include_private=False
        )
        
        assert "_private_method" not in result.output

    @pytest.mark.asyncio
    async def test_include_private_members(self, doc_tool, sample_python_file):
        """Test including private members."""
        doc_tool.workspace = sample_python_file.parent
        
        result = await doc_tool.execute(
            action="extract",
            file_path=str(sample_python_file),
            format="text",
            include_private=True
        )
        
        assert "_private_method" in result.output

    @pytest.mark.asyncio
    async def test_generate_to_file(self, doc_tool, sample_python_file):
        """Test generating documentation to a file."""
        doc_tool.workspace = sample_python_file.parent
        output_path = sample_python_file.parent / "docs.md"
        
        result = await doc_tool.execute(
            action="generate",
            file_path=str(sample_python_file),
            format="markdown",
            output_path=str(output_path)
        )
        
        assert "Documentation written to" in result.output
        assert output_path.exists()

    @pytest.mark.asyncio
    async def test_unsupported_file_type(self, doc_tool, tmp_path):
        """Test unsupported file type."""
        unsupported = tmp_path / "file.xyz"
        unsupported.write_text("some content")
        
        doc_tool.workspace = tmp_path
        
        result = await doc_tool.execute(
            action="extract",
            file_path=str(unsupported),
            format="text"
        )
        
        assert "Error" in result.output or "unsupported" in result.output.lower()

    @pytest.mark.asyncio
    async def test_invalid_action(self, doc_tool, sample_python_file):
        """Test invalid action."""
        doc_tool.workspace = sample_python_file.parent
        
        result = await doc_tool.execute(
            action="invalid",
            file_path=str(sample_python_file)
        )
        
        assert "Error" in result.output or "unknown action" in result.output.lower()

    def test_documentation_tool_schema(self, doc_tool):
        """Test that documentation tool has proper schema."""
        schema = doc_tool.schema()
        
        assert schema["name"] == "documentation"
        assert "description" in schema
        assert "parameters" in schema
        assert "action" in schema["parameters"]["properties"]
        assert "file_path" in schema["parameters"]["properties"]
