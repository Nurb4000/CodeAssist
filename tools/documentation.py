import ast
import inspect
import logging
import re
from pathlib import Path
from typing import Any

from tools import Tool, ToolResult
from tools.security import validate_path, WorkspaceViolationError

log = logging.getLogger(__name__)


class DocumentationTool(Tool):
    name = "documentation"
    description = (
        "Generate documentation from source code. Supports Python, JavaScript, TypeScript, "
        "and other languages. Extracts functions, classes, methods, and their signatures, "
        "parameters, and docstrings. Outputs in Markdown, JSDoc, or reStructuredText format."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["generate", "extract", "update"],
                "description": "Documentation action to perform",
            },
            "file_path": {
                "type": "string",
                "description": "Path to source file",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "jsdoc", "rst", "text"],
                "description": "Output format",
            },
            "include_private": {
                "type": "boolean",
                "description": "Include private members (starting with _)",
            },
            "include_dunders": {
                "type": "boolean",
                "description": "Include dunder methods (__method__) for Python",
            },
            "output_path": {
                "type": "string",
                "description": "Path to write documentation (for generate action)",
            },
        },
        "required": ["action", "file_path"],
    }

    def __init__(self):
        self.workspace = Path.cwd()

    async def execute(self, action: str, file_path: str, format: str = "markdown",
                     include_private: bool = False, include_dunders: bool = False,
                     output_path: str | None = None) -> ToolResult:
        try:
            path = Path(file_path).resolve()
            validate_path(path, self.workspace)

            if action == "generate":
                return await self._generate(path, format, include_private, include_dunders, output_path)
            elif action == "extract":
                return await self._extract(path, format, include_private, include_dunders)
            elif action == "update":
                return await self._update(path, format, include_private, include_dunders)
            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except WorkspaceViolationError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            log.exception("Documentation generation failed")
            return ToolResult(output=f"Documentation error: {e}", error=True)

    async def _generate(self, path: Path, format: str, include_private: bool,
                       include_dunders: bool, output_path: str | None) -> ToolResult:
        """Generate documentation from source file."""
        ext = path.suffix.lower()
        
        if ext == ".py":
            docs = self._extract_python(path, include_private, include_dunders)
        elif ext in [".js", ".ts", ".jsx", ".tsx"]:
            docs = self._extract_javascript(path, include_private, include_dunders)
        else:
            return ToolResult(output=f"Error: unsupported file type '{ext}'", error=True)

        if not docs:
            return ToolResult(output=f"No documentable items found in {path.name}")

        formatted = self._format_docs(docs, format)

        if output_path:
            out = Path(output_path).resolve()
            validate_path(out, self.workspace)
            out.write_text(formatted, encoding="utf-8")
            return ToolResult(output=f"Documentation written to {out}")

        return ToolResult(output=formatted)

    async def _extract(self, path: Path, format: str, include_private: bool,
                      include_dunders: bool) -> ToolResult:
        """Extract documentation without formatting."""
        ext = path.suffix.lower()
        
        if ext == ".py":
            docs = self._extract_python(path, include_private, include_dunders)
        elif ext in [".js", ".ts", ".jsx", ".tsx"]:
            docs = self._extract_javascript(path, include_private, include_dunders)
        else:
            return ToolResult(output=f"Error: unsupported file type '{ext}'", error=True)

        if not docs:
            return ToolResult(output=f"No documentable items found in {path.name}")

        return ToolResult(output=self._format_docs(docs, format))

    async def _update(self, path: Path, format: str, include_private: bool,
                     include_dunders: bool) -> ToolResult:
        """Update existing documentation file."""
        doc_path = path.with_name(f"{path.stem}.md")
        
        if not doc_path.exists():
            return ToolResult(output=f"Documentation file not found: {doc_path}", error=True)

        ext = path.suffix.lower()
        if ext == ".py":
            docs = self._extract_python(path, include_private, include_dunders)
        else:
            return ToolResult(output=f"Error: update only supported for Python files", error=True)

        if not docs:
            return ToolResult(output=f"No documentable items found in {path.name}")

        formatted = self._format_docs(docs, "markdown")
        doc_path.write_text(formatted, encoding="utf-8")

        return ToolResult(output=f"Updated documentation: {doc_path}")

    def _extract_python(self, path: Path, include_private: bool, include_dunders: bool) -> list[dict]:
        """Extract documentation from Python file."""
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception as e:
            log.error("Failed to parse Python file: %s", e)
            return []

        docs = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Skip private methods unless requested
                if not include_private and node.name.startswith('_'):
                    continue
                
                # Skip dunder methods unless requested
                if not include_dunders and node.name.startswith('__') and node.name.endswith('__'):
                    continue

                doc = self._extract_function_doc(node)
                docs.append(doc)

            elif isinstance(node, ast.ClassDef):
                # Skip private classes unless requested
                if not include_private and node.name.startswith('_'):
                    continue

                doc = {
                    "type": "class",
                    "name": node.name,
                    "docstring": ast.get_docstring(node) or "",
                    "methods": [],
                }

                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        if not include_private and item.name.startswith('_'):
                            continue
                        if not include_dunders and item.name.startswith('__') and item.name.endswith('__'):
                            continue
                        
                        method_doc = self._extract_function_doc(item)
                        doc["methods"].append(method_doc)

                docs.append(doc)

        return docs

    def _extract_function_doc(self, node: ast.FunctionDef) -> dict:
        """Extract documentation from a function node."""
        docstring = ast.get_docstring(node) or ""
        
        # Extract parameters
        params = []
        for arg in node.args.args:
            if arg.arg == 'self':
                continue
            param = {"name": arg.arg}
            if arg.annotation:
                param["type"] = self._annotation_to_str(arg.annotation)
            params.append(param)

        # Extract return type
        return_type = None
        if node.returns:
            return_type = self._annotation_to_str(node.returns)

        return {
            "type": "function",
            "name": node.name,
            "docstring": docstring,
            "params": params,
            "return_type": return_type,
            "line": node.lineno,
        }

    def _annotation_to_str(self, annotation: ast.expr) -> str:
        """Convert AST annotation to string."""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Attribute):
            return f"{self._annotation_to_str(annotation.value)}.{annotation.attr}"
        elif isinstance(annotation, ast.Subscript):
            value = self._annotation_to_str(annotation.value)
            slice_val = self._annotation_to_str(annotation.slice)
            return f"{value}[{slice_val}]"
        elif isinstance(annotation, ast.Tuple):
            elts = ", ".join(self._annotation_to_str(e) for e in annotation.elts)
            return f"({elts})"
        else:
            return "Any"

    def _extract_javascript(self, path: Path, include_private: bool, include_dunders: bool) -> list[dict]:
        """Extract documentation from JavaScript/TypeScript file."""
        source = path.read_text(encoding="utf-8")
        docs = []

        # Simple regex-based extraction (for complex cases, use a proper parser)
        # Match function declarations
        func_pattern = re.compile(
            r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)',
            re.MULTILINE
        )

        for match in func_pattern.finditer(source):
            name = match.group(1)
            params_str = match.group(2)

            if not include_private and name.startswith('_'):
                continue

            # Extract JSDoc comment before function
            start = match.start()
            comment_match = re.search(r'/\*\*(.*?)\*/', source[:start][::-1], re.DOTALL)
            docstring = ""
            if comment_match:
                docstring = comment_match.group(1).replace('*', '').strip()

            # Parse parameters
            params = []
            if params_str.strip():
                for param in params_str.split(','):
                    param = param.strip()
                    if ':' in param:  # TypeScript type annotation
                        name_part, type_part = param.split(':', 1)
                        params.append({
                            "name": name_part.strip(),
                            "type": type_part.strip()
                        })
                    else:
                        params.append({"name": param})

            docs.append({
                "type": "function",
                "name": name,
                "docstring": docstring,
                "params": params,
                "return_type": None,
                "line": source[:start].count('\n') + 1,
            })

        # Match class declarations
        class_pattern = re.compile(
            r'(?:export\s+)?class\s+(\w+)',
            re.MULTILINE
        )

        for match in class_pattern.finditer(source):
            name = match.group(1)

            if not include_private and name.startswith('_'):
                continue

            # Extract JSDoc comment before class
            start = match.start()
            comment_match = re.search(r'/\*\*(.*?)\*/', source[:start][::-1], re.DOTALL)
            docstring = ""
            if comment_match:
                docstring = comment_match.group(1).replace('*', '').strip()

            docs.append({
                "type": "class",
                "name": name,
                "docstring": docstring,
                "methods": [],
            })

        return docs

    def _format_docs(self, docs: list[dict], format: str) -> str:
        """Format documentation in the specified format."""
        if format == "markdown":
            return self._format_markdown(docs)
        elif format == "jsdoc":
            return self._format_jsdoc(docs)
        elif format == "rst":
            return self._format_rst(docs)
        else:
            return self._format_text(docs)

    def _format_markdown(self, docs: list[dict]) -> str:
        """Format as Markdown."""
        lines = [f"# Documentation\n"]

        for doc in docs:
            if doc["type"] == "class":
                lines.append(f"## Class: {doc['name']}\n")
                if doc["docstring"]:
                    lines.append(f"{doc['docstring']}\n")
                
                if doc.get("methods"):
                    lines.append("\n### Methods\n")
                    for method in doc["methods"]:
                        lines.append(f"#### `{method['name']}`")
                        if method.get("params"):
                            lines.append(f"\n**Parameters:**\n")
                            for param in method["params"]:
                                type_str = f" (`{param['type']}`)" if param.get("type") else ""
                                lines.append(f"- `{param['name']}`{type_str}")
                        if method.get("return_type"):
                            lines.append(f"\n**Returns:** `{method['return_type']}`")
                        if method.get("docstring"):
                            lines.append(f"\n{method['docstring']}\n")
            
            elif doc["type"] == "function":
                lines.append(f"## Function: {doc['name']}\n")
                if doc.get("params"):
                    lines.append(f"**Parameters:**\n")
                    for param in doc["params"]:
                        type_str = f" (`{param['type']}`)" if param.get("type") else ""
                        lines.append(f"- `{param['name']}`{type_str}")
                if doc.get("return_type"):
                    lines.append(f"\n**Returns:** `{doc['return_type']}`")
                if doc.get("docstring"):
                    lines.append(f"\n{doc['docstring']}\n")

        return "\n".join(lines)

    def _format_jsdoc(self, docs: list[dict]) -> str:
        """Format as JSDoc."""
        lines = []

        for doc in docs:
            if doc["type"] == "class":
                lines.append(f"/**")
                if doc["docstring"]:
                    lines.append(f" * {doc['docstring']}")
                lines.append(" */")
                lines.append(f"class {doc['name']} {{}}\n")
            
            elif doc["type"] == "function":
                lines.append(f"/**")
                if doc.get("docstring"):
                    lines.append(f" * {doc['docstring']}")
                if doc.get("params"):
                    for param in doc["params"]:
                        type_str = param.get("type", "")
                        lines.append(f" * @param {{{type_str}}} {param['name']}")
                if doc.get("return_type"):
                    lines.append(f" * @returns {{{doc['return_type']}}}")
                lines.append(" */")
                lines.append(f"function {doc['name']}() {{}}\n")

        return "\n".join(lines)

    def _format_rst(self, docs: list[dict]) -> str:
        """Format as reStructuredText."""
        lines = []

        for doc in docs:
            if doc["type"] == "class":
                lines.append(f"{doc['name']}")
                lines.append("=" * len(doc['name']))
                if doc["docstring"]:
                    lines.append(f"\n{doc['docstring']}\n")
            
            elif doc["type"] == "function":
                lines.append(f"{doc['name']}()")
                lines.append("-" * len(doc['name']))
                if doc.get("params"):
                    lines.append("\n**Parameters:**\n")
                    for param in doc["params"]:
                        type_str = param.get("type", "Any")
                        lines.append(f":param {param['name']}: {type_str}")
                if doc.get("return_type"):
                    lines.append(f"\n:returns: {doc['return_type']}")
                if doc.get("docstring"):
                    lines.append(f"\n{doc['docstring']}\n")

        return "\n".join(lines)

    def _format_text(self, docs: list[dict]) -> str:
        """Format as plain text."""
        lines = []

        for doc in docs:
            if doc["type"] == "class":
                lines.append(f"CLASS: {doc['name']}")
                if doc["docstring"]:
                    lines.append(f"  {doc['docstring']}")
            
            elif doc["type"] == "function":
                lines.append(f"FUNCTION: {doc['name']}")
                if doc.get("params"):
                    lines.append(f"  Parameters: {', '.join(p['name'] for p in doc['params'])}")
                if doc.get("return_type"):
                    lines.append(f"  Returns: {doc['return_type']}")
                if doc.get("docstring"):
                    lines.append(f"  {doc['docstring']}")

        return "\n".join(lines)
