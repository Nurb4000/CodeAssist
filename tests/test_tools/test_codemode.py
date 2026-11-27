"""Tests for the confined Code Mode execute tool (D1)."""

import pytest

from codeassist.tools import Tool, ToolRegistry, ToolResult
from codeassist.tools.codemode import ExecuteTool


class EchoTool(Tool):
    name = "echo"
    description = "Echo text"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(self, text: str) -> ToolResult:
        return ToolResult(output=f"echo:{text}")


class AddTool(Tool):
    name = "math.add"
    description = "Add numbers"
    parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
    }

    async def execute(self, a: int, b: int) -> ToolResult:
        return ToolResult(output=str(a + b))


class FailTool(Tool):
    name = "fail"
    description = "Always fails"
    parameters = {"type": "object", "properties": {}}

    async def execute(self) -> ToolResult:
        return ToolResult(output="boom", error=True)


def _registry() -> ToolRegistry:
    reg = ToolRegistry(workspace=None)
    reg.register(EchoTool())
    reg.register(AddTool())
    reg.register(FailTool())
    ex = ExecuteTool()
    ex.set_registry(reg)
    reg.register(ex)
    return reg


@pytest.mark.asyncio
async def test_execute_simple_return():
    reg = _registry()
    result = await reg.execute("execute", {"code": "return 1 + 2"})
    assert not result.error, result.output
    assert result.output.strip() == "3"


@pytest.mark.asyncio
async def test_execute_tool_call():
    reg = _registry()
    result = await reg.execute("execute", {"code": "const r = await tools.echo({text: 'hi'}); return r;"})
    assert not result.error, result.output
    assert "echo:hi" in result.output


@pytest.mark.asyncio
async def test_execute_namespaced_tool_call():
    reg = _registry()
    result = await reg.execute("execute", {"code": "return await tools['math.add']({a: 2, b: 5});"})
    assert not result.error, result.output
    assert result.output.strip() == "7"


@pytest.mark.asyncio
async def test_execute_tool_error_surfaces():
    reg = _registry()
    result = await reg.execute(
        "execute",
        {"code": "try { await tools.fail({}); return 'ok'; } catch (e) { return String(e.message || e); }"},
    )
    assert not result.error, result.output
    assert "boom" in result.output


@pytest.mark.asyncio
async def test_execute_blocks_recursive_execute():
    reg = _registry()
    result = await reg.execute(
        "execute",
        {"code": "try { return await tools.execute({code: 'return 1'}); } catch (e) { return String(e.message || e); }"},
    )
    assert not result.error, result.output
    assert "not available" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_requires_code():
    reg = _registry()
    result = await reg.execute("execute", {"code": ""})
    assert result.error


@pytest.mark.asyncio
async def test_execute_permission_executor_blocks():
    reg = _registry()
    ex = reg.get("execute")

    async def deny(name, args):
        return ToolResult(output="denied by test", error=True)

    ex.set_executor(deny)
    try:
        result = await reg.execute(
            "execute",
            {"code": "try { return await tools.echo({text:'x'}); } catch (e) { return String(e.message || e); }"},
        )
    finally:
        ex.set_executor(None)
    assert not result.error, result.output
    assert "denied by test" in result.output


@pytest.mark.asyncio
async def test_execute_timeout():
    reg = _registry()
    result = await reg.execute("execute", {"code": "while(true) {}", "timeout_ms": 200})
    assert result.error
    assert "timed out" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_disabled_by_config():
    reg = ToolRegistry(workspace=None)
    reg.register(EchoTool())
    ex = ExecuteTool()
    ex.enabled = False
    ex.set_registry(reg)
    reg.register(ex)
    result = await reg.execute("execute", {"code": "return 1"})
    assert result.error
    assert "disabled" in result.output.lower()
