"""
CodeAssist Session Hook - Auto-generates session summaries and extracts knowledge.

Called when a session ends (WebSocket disconnect) to:
1. Generate a session summary
2. Extract knowledge entries
3. Log file snapshots
4. Detect patterns for auto-creation
"""

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from session import Session
from knowledge import KnowledgeBase

log = logging.getLogger(__name__)

# File extensions to track
TRACKED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".java", ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h",
    ".sql", ".sh", ".bash", ".zsh",
    ".yaml", ".yml", ".toml", ".json", ".xml",
    ".html", ".css", ".scss", ".less",
    ".md", ".rst", ".txt",
    ".dockerfile", ".docker-compose",
}

# Patterns to extract from code
CODE_PATTERNS = {
    "error_handling": [
        r"try:\s*\n\s+.*except\s+\w+",
        r"catch\s*\([^)]+\)\s*\{",
        r"\.catch\(",
        r"raise\s+\w+Error",
        r"error\s*=\s*None",
    ],
    "async_patterns": [
        r"async\s+def\s+\w+",
        r"await\s+\w+",
        r"asyncio\.\w+",
        r"\.then\(",
        r"Promise\.",
    ],
    "database_patterns": [
        r"SELECT\s+.*\s+FROM\s+",
        r"INSERT\s+INTO\s+",
        r"UPDATE\s+.*\s+SET\s+",
        r"DELETE\s+FROM\s+",
        r"\.execute\(",
        r"\.fetchall\(",
        r"\.fetchone\(",
    ],
    "api_patterns": [
        r"@app\.(get|post|put|delete|patch)\(",
        r"router\.\w+\(",
        r"fetch\(",
        r"axios\.\w+\(",
        r"requests\.\w+\(",
    ],
    "config_patterns": [
        r"os\.environ",
        r"os\.getenv",
        r"process\.env\.",
        r"config\s*=\s*\{",
        r"settings\s*=\s*\{",
    ],
    "test_patterns": [
        r"def\s+test_\w+",
        r"describe\(",
        r"it\(",
        r"expect\(",
        r"assert\s+",
    ],
    "import_patterns": [
        r"from\s+\w+\s+import\s+",
        r"import\s+\w+",
        r"require\(",
        r"import\s+.*\s+from\s+",
    ],
}

# Indicators for knowledge extraction
KNOWLEDGE_INDICATORS = {
    "pattern": [
        "pattern", "convention", "standard", "approach", "method",
        "typically", "usually", "generally", "commonly", "should",
        "recommend", "best practice", "approach", "way to",
    ],
    "decision": [
        "decided", "chose", "selected", "will use", "going with",
        "opted for", "selected", "picked", "settled on",
    ],
    "convention": [
        "convention", "naming", "structure", "format", "style",
        "organized", "arranged", "named", "labeled",
    ],
    "bug_fix": [
        "bug", "fix", "issue", "problem", "error", "broken",
        "was failing", "wasn't working", "resolved", "fixed by",
    ],
    "optimization": [
        "optimize", "performance", "faster", "speed up", "improve",
        "efficient", "cached", "batch", "parallel", "async",
    ],
}


class SessionHook:
    """Handles post-session processing for knowledge base population."""
    
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self._session_content_hashes: set[str] = set()
    
    async def on_session_end(self, session: Session, agent=None):
        """Called when a session ends. Generates summary and extracts knowledge."""
        self._session_content_hashes.clear()
        try:
            # Get session messages
            messages = await session.get_messages()
            if not messages:
                return
            
            # Calculate session stats
            stats = self._calculate_stats(messages)
            
            # Generate summary using LLM if available, otherwise use simple extraction
            if self.llm_client:
                summary_data = await self._generate_llm_summary(session.id, messages, stats)
            else:
                summary_data = self._generate_simple_summary(messages, stats)
            
            # Store session summary
            await KnowledgeBase.create_session_summary(
                session_id=session.id,
                summary=summary_data["summary"],
                key_topics=summary_data.get("topics", []),
                goals_achieved=summary_data.get("goals", []),
                tools_used=summary_data.get("tools_used", []),
                files_modified=summary_data.get("files_modified", []),
                duration_seconds=stats["duration_seconds"],
                message_count=stats["message_count"],
                token_usage=stats.get("total_tokens"),
                model=stats.get("model"),
                quality_score=self._calculate_quality_score(stats),
            )
            
            log.info("Session summary created for %s", session.id)
            
            # Extract knowledge entries
            await self._extract_knowledge(session.id, messages, summary_data)
            
        except Exception as e:
            log.exception("Error generating session summary for %s", session.id)
    
    def _calculate_stats(self, messages: list[dict]) -> dict:
        """Calculate basic statistics from messages."""
        user_msgs = [m for m in messages if m["role"] == "user"]
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        
        # Extract tool names from assistant messages
        tools_used = set()
        for msg in assistant_msgs:
            if msg.get("tool_calls"):
                try:
                    tool_calls = json.loads(msg["tool_calls"])
                    for tc in tool_calls:
                        if isinstance(tc, dict) and "function" in tc:
                            tools_used.add(tc["function"].get("name", ""))
                        elif isinstance(tc, dict):
                            tools_used.add(tc.get("name", ""))
                except (json.JSONDecodeError, TypeError):
                    pass
        
        # Extract file paths from messages
        files_modified = set()
        for msg in messages:
            content = msg.get("content", "") or ""
            files_modified.update(self._extract_file_paths(content))
        
        # Calculate duration from timestamps
        duration_seconds = 0
        if messages:
            try:
                first_ts = messages[0].get("created_at", "")
                last_ts = messages[-1].get("created_at", "")
                if first_ts and last_ts:
                    first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                    last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    duration_seconds = int((last_dt - first_dt).total_seconds())
            except (ValueError, TypeError):
                pass
        
        return {
            "message_count": len(messages),
            "user_message_count": len(user_msgs),
            "assistant_message_count": len(assistant_msgs),
            "tool_call_count": len(tool_msgs),
            "tools_used": list(tools_used),
            "files_modified": list(files_modified),
            "duration_seconds": duration_seconds,
            "first_user_message": user_msgs[0]["content"][:500] if user_msgs else None,
            "last_user_message": user_msgs[-1]["content"][:500] if user_msgs else None,
        }
    
    def _extract_file_paths(self, content: str) -> set:
        """Extract file paths from content."""
        files = set()
        if not content:
            return files
        
        # Match common file path patterns
        patterns = [
            r'["\']([/\\]?[\w./\\-]+\.\w{1,10})["\']',  # Quoted paths
            r'(?:^|\s)([/\\]?[\w./\\-]+\.\w{1,10})(?:\s|$)',  # Bare paths
            r'(?:file|path|read|write|edit)\s*[=:]\s*["\']?([/\\]?[\w./\\-]+\.\w{1,10})',  # Assignment contexts
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                path = match.group(1)
                # Check if it has a tracked extension
                suffix = Path(path).suffix.lower()
                if suffix in TRACKED_EXTENSIONS:
                    # Clean up the path
                    clean = path.strip('"\'')
                    if len(clean) < 300 and "/" in clean:
                        files.add(clean)
        
        return files
    
    def _generate_simple_summary(self, messages: list[dict], stats: dict) -> dict:
        """Generate a simple summary without LLM."""
        tools = stats["tools_used"]
        files = stats["files_modified"]
        duration = stats["duration_seconds"]
        
        # Build summary from first and last messages
        first_msg = stats.get("first_user_message", "")
        last_msg = stats.get("last_user_message", "")
        
        summary_parts = []
        if first_msg:
            summary_parts.append(f"Session started with: {first_msg[:200]}")
        if tools:
            summary_parts.append(f"Used tools: {', '.join(tools[:5])}")
        if files:
            summary_parts.append(f"Modified files: {', '.join(files[:5])}")
        if duration > 0:
            mins = duration // 60
            summary_parts.append(f"Duration: {mins} minutes" if mins > 0 else f"Duration: {duration} seconds")
        
        summary = ". ".join(summary_parts) if summary_parts else "Empty session"
        
        # Extract topics from first message
        topics = []
        if first_msg:
            # Simple keyword extraction
            words = first_msg.lower().split()
            topics = [w for w in words if len(w) > 4 and w.isalpha()][:5]
        
        return {
            "summary": summary[:1000],
            "topics": topics,
            "goals": [],
            "tools_used": tools,
            "files_modified": files,
        }
    
    async def _generate_llm_summary(self, session_id: str, messages: list[dict], stats: dict) -> dict:
        """Generate summary using LLM."""
        try:
            # Build context for summary generation
            context_messages = []
            for msg in messages[:20]:  # Limit to first 20 messages
                role = msg["role"]
                content = (msg.get("content", "") or "")[:500]
                if content:
                    context_messages.append({"role": role, "content": content})
            
            # Create summary prompt
            prompt = f"""Summarize this coding session in 2-3 sentences.
Include: what was accomplished, which tools were used, and any key files modified.

Session statistics:
- Messages: {stats['message_count']}
- Tools used: {', '.join(stats['tools_used'][:5])}
- Files modified: {', '.join(stats['files_modified'][:5])}

Provide a JSON response with:
- summary: 2-3 sentence summary
- topics: list of 3-5 key topics
- goals: list of goals achieved (if any)
"""
            
            # Use the LLM client if available
            if hasattr(self.llm_client, 'complete'):
                response = await self.llm_client.complete(prompt, max_tokens=500)
                try:
                    return json.loads(response)
                except json.JSONDecodeError:
                    return {"summary": response[:1000], "topics": [], "goals": []}
            
            # Fallback to simple summary
            return self._generate_simple_summary(messages, stats)
            
        except Exception as e:
            log.warning("LLM summary generation failed, using simple summary: %s", e)
            return self._generate_simple_summary(messages, stats)
    
    def _calculate_quality_score(self, stats: dict) -> float:
        """Calculate a quality score for the session (0.0 - 1.0)."""
        score = 0.0
        
        # Factor 1: Session duration (longer = more substantial)
        duration = stats.get("duration_seconds", 0)
        if duration > 3600:
            score += 0.3
        elif duration > 600:
            score += 0.2
        elif duration > 60:
            score += 0.1
        
        # Factor 2: Number of messages (more = more interaction)
        msg_count = stats.get("message_count", 0)
        if msg_count > 20:
            score += 0.3
        elif msg_count > 10:
            score += 0.2
        elif msg_count > 5:
            score += 0.1
        
        # Factor 3: Tool usage (more tools = more productive)
        tools_count = len(stats.get("tools_used", []))
        if tools_count > 5:
            score += 0.2
        elif tools_count > 2:
            score += 0.1
        
        # Factor 4: Files modified (indicates actual work done)
        files_count = len(stats.get("files_modified", []))
        if files_count > 3:
            score += 0.2
        elif files_count > 0:
            score += 0.1
        
        return min(score, 1.0)
    
    async def _extract_knowledge(self, session_id: str, messages: list[dict], summary_data: dict):
        """Extract knowledge entries from session messages."""
        try:
            extracted = []
            
            # 1. Extract from tool calls
            tool_knowledge = await self._extract_from_tool_calls(session_id, messages)
            extracted.extend(tool_knowledge)
            
            # 2. Extract from code patterns in assistant messages
            code_knowledge = await self._extract_from_code_patterns(session_id, messages)
            extracted.extend(code_knowledge)
            
            # 3. Extract from user questions (what people ask about)
            question_knowledge = await self._extract_from_user_questions(session_id, messages)
            extracted.extend(question_knowledge)
            
            # 4. Extract error handling patterns
            error_knowledge = await self._extract_from_errors(session_id, messages)
            extracted.extend(error_knowledge)
            
            # 5. Extract file structure knowledge
            file_knowledge = await self._extract_from_file_operations(session_id, messages)
            extracted.extend(file_knowledge)
            
            # 6. Detect repetitive patterns for auto-creation
            pattern_knowledge = await self._detect_repetitive_patterns(session_id, messages)
            extracted.extend(pattern_knowledge)
            
            log.info("Knowledge extraction completed for session %s: %d entries extracted", session_id, len(extracted))
            
        except Exception as e:
            log.exception("Error extracting knowledge from session %s", session_id)
    
    async def _extract_from_tool_calls(self, session_id: str, messages: list[dict]) -> list[dict]:
        """Extract knowledge from tool call patterns."""
        extracted = []
        
        for msg in messages:
            if not msg.get("tool_calls"):
                continue
            
            try:
                tool_calls = json.loads(msg["tool_calls"])
            except (json.JSONDecodeError, TypeError):
                continue
            
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                args_str = func.get("arguments", "{}")
                
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    continue
                
                # Extract from write/edit operations
                if tool_name in ("write", "edit"):
                    file_path = args.get("file_path", args.get("path", ""))
                    if file_path:
                        # Log file snapshot
                        await KnowledgeBase.log_file_snapshot(
                            session_id=session_id,
                            file_path=file_path,
                            action=tool_name,
                            content_preview=str(args.get("content", ""))[:500],
                        )
                        
                        # Extract file type knowledge
                        suffix = Path(file_path).suffix.lower()
                        if suffix:
                            await self._create_knowledge_if_new(
                                entry_type="convention",
                                scope="file",
                                scope_identifier=file_path,
                                content=f"File type: {suffix} - {tool_name} operation performed",
                                source_session_id=session_id,
                                tags=["file_type", suffix.lstrip(".")],
                                confidence=0.6,
                            )
                
                # Extract from shell commands
                elif tool_name == "shell":
                    command = args.get("command", "")
                    if command:
                        knowledge = self._analyze_shell_command(command)
                        if knowledge:
                            await self._create_knowledge_if_new(**knowledge, source_session_id=session_id)
                            extracted.append(knowledge)
                
                # Extract from read operations (shows what files are important)
                elif tool_name == "read":
                    file_path = args.get("file_path", args.get("path", ""))
                    if file_path:
                        await KnowledgeBase.log_file_snapshot(
                            session_id=session_id,
                            file_path=file_path,
                            action="read",
                        )
                        suffix = Path(file_path).suffix.lower()
                        if suffix:
                            await self._create_knowledge_if_new(
                                entry_type="convention",
                                scope="file",
                                scope_identifier=file_path,
                                content=f"File reviewed: {file_path} ({suffix} file examined)",
                                source_session_id=session_id,
                                tags=["file_review", suffix.lstrip(".")],
                                confidence=0.5,
                            )
                
                # Extract from directory/glob/grep operations (project exploration)
                elif tool_name in ("directory", "glob", "grep", "ls", "find"):
                    pattern = args.get("pattern", args.get("query", args.get("path", "")))
                    if pattern:
                        knowledge = {
                            "entry_type": "pattern",
                            "scope": "project",
                            "content": f"Project exploration via {tool_name}: searched for '{pattern[:200]}'",
                            "tags": ["exploration", tool_name],
                            "confidence": 0.4,
                        }
                        await self._create_knowledge_if_new(**knowledge, source_session_id=session_id)
                        extracted.append(knowledge)
                
                # Extract from web search/fetch operations
                elif tool_name in ("websearch", "webfetch"):
                    query = args.get("query", args.get("url", ""))
                    if query:
                        knowledge = {
                            "entry_type": "pattern",
                            "scope": "project",
                            "content": f"External research via {tool_name}: '{query[:200]}'",
                            "tags": ["research", tool_name],
                            "confidence": 0.4,
                        }
                        await self._create_knowledge_if_new(**knowledge, source_session_id=session_id)
                        extracted.append(knowledge)
        
        return extracted
    
    def _analyze_shell_command(self, command: str) -> dict | None:
        """Analyze a shell command and extract knowledge patterns."""
        command_lower = command.lower()
        
        # Test commands
        if any(cmd in command_lower for cmd in ["pytest", "test", "unittest", "jest", "mocha"]):
            return {
                "entry_type": "pattern",
                "scope": "project",
                "content": f"Test command pattern: {command[:300]}",
                "tags": ["testing", "command", "shell"],
                "confidence": 0.7,
            }
        
        # Git commands
        if "git" in command_lower:
            return {
                "entry_type": "pattern",
                "scope": "project",
                "content": f"Git workflow pattern: {command[:300]}",
                "tags": ["git", "workflow", "shell"],
                "confidence": 0.7,
            }
        
        # Package management
        if any(cmd in command_lower for cmd in ["pip install", "npm install", "yarn add", "cargo add"]):
            return {
                "entry_type": "pattern",
                "scope": "project",
                "content": f"Package installation: {command[:300]}",
                "tags": ["dependencies", "package_management", "shell"],
                "confidence": 0.7,
            }
        
        # Build/compile commands
        if any(cmd in command_lower for cmd in ["make", "cargo build", "npm run build", "webpack"]):
            return {
                "entry_type": "pattern",
                "scope": "project",
                "content": f"Build pattern: {command[:300]}",
                "tags": ["build", "compile", "shell"],
                "confidence": 0.7,
            }
        
        # Docker commands
        if "docker" in command_lower:
            return {
                "entry_type": "pattern",
                "scope": "project",
                "content": f"Docker pattern: {command[:300]}",
                "tags": ["docker", "container", "shell"],
                "confidence": 0.7,
            }
        
        return None
    
    async def _extract_from_code_patterns(self, session_id: str, messages: list[dict]) -> list[dict]:
        """Extract knowledge from code patterns and analysis in assistant messages."""
        extracted = []
        
        for msg in messages:
            if msg["role"] != "assistant":
                continue
            
            content = msg.get("content", "") or ""
            if len(content) < 50:
                continue
            
            # Detect code patterns via regex
            for pattern_name, patterns in CODE_PATTERNS.items():
                for regex in patterns:
                    if re.search(regex, content, re.MULTILINE):
                        snippet = self._extract_snippet_around_match(content, regex)
                        if snippet:
                            knowledge = self._classify_and_create_knowledge(
                                content=snippet,
                                pattern_type=pattern_name,
                                session_id=session_id,
                            )
                            if knowledge:
                                knowledge["source_session_id"] = session_id
                                await self._create_knowledge_if_new(**knowledge)
                                extracted.append(knowledge)
                        break  # Don't match same pattern type multiple times
            
            # Extract insights from assistant analysis (reviews, suggestions, explanations)
            analysis_keywords = [
                "recommend", "suggestion", "improvement", "should", "could",
                "consider", "best practice", "issue", "problem", "concern",
                "observation", "note that", "important", "warning",
            ]
            content_lower = content.lower()
            if any(kw in content_lower for kw in analysis_keywords) and len(content) > 100:
                # Extract the most informative paragraph
                paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 80]
                if paragraphs:
                    snippet = paragraphs[0][:500]
                    knowledge = {
                        "entry_type": "pattern",
                        "scope": "project",
                        "content": f"Assistant analysis: {snippet}",
                        "tags": ["analysis", "review", "insight"],
                        "confidence": 0.5,
                        "source_session_id": session_id,
                    }
                    await self._create_knowledge_if_new(**knowledge)
                    extracted.append(knowledge)
        
        return extracted
    
    def _extract_snippet_around_match(self, content: str, regex: str, context_chars: int = 200) -> str | None:
        """Extract a snippet around a regex match."""
        match = re.search(regex, content, re.MULTILINE)
        if not match:
            return None
        
        start = max(0, match.start() - context_chars)
        end = min(len(content), match.end() + context_chars)
        
        snippet = content[start:end].strip()
        
        # Clean up snippet
        lines = snippet.split("\n")
        # Remove empty lines at start/end
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        
        return "\n".join(lines[:10])  # Limit to 10 lines
    
    def _classify_and_create_knowledge(self, content: str, pattern_type: str, session_id: str) -> dict | None:
        """Classify content and create knowledge entry."""
        content_lower = content.lower()
        
        # Check for knowledge indicators
        for knowledge_type, indicators in KNOWLEDGE_INDICATORS.items():
            if any(indicator in content_lower for indicator in indicators):
                tags = [knowledge_type, pattern_type]
                
                # Add more specific tags based on content
                if "import" in content_lower or "require" in content_lower:
                    tags.append("imports")
                if "class" in content_lower:
                    tags.append("class")
                if "function" in content_lower or "def " in content_lower:
                    tags.append("function")
                if "test" in content_lower:
                    tags.append("testing")
                
                return {
                    "entry_type": knowledge_type,
                    "scope": "project",
                    "content": content[:1000],
                    "tags": tags,
                    "confidence": 0.7,
                }
        
        # Default to pattern if no specific indicator found
        if pattern_type in ("error_handling", "async_patterns", "database_patterns"):
            return {
                "entry_type": "pattern",
                "scope": "project",
                "content": content[:1000],
                "tags": [pattern_type, "code_pattern"],
                "confidence": 0.6,
            }
        
        return None
    
    async def _extract_from_user_questions(self, session_id: str, messages: list[dict]) -> list[dict]:
        """Extract knowledge from user questions and requests."""
        extracted = []
        
        user_msgs = [m for m in messages if m["role"] == "user" and m.get("content")]
        
        for msg in user_msgs:
            content = msg.get("content", "") or ""
            if len(content) < 20:
                continue
            
            # Extract from questions and requests
            is_question = (
                content.strip().endswith("?")
                or any(word in content.lower() for word in [
                    "how", "what", "why", "where", "when", "which", "who",
                ])
                or any(word in content.lower() for word in [
                    "can you", "could you", "please", "help me", "show me",
                    "explain", "review", "check", "look at", "find",
                    "tell me", "describe", "analyze",
                ])
            )
            
            if is_question:
                topic = self._extract_topic_from_question(content)
                if topic:
                    knowledge = {
                        "entry_type": "pattern",
                        "scope": "project",
                        "content": f"User request pattern: {content[:300]}",
                        "tags": ["user_request", topic],
                        "confidence": 0.5,
                        "source_session_id": session_id,
                    }
                    await self._create_knowledge_if_new(**knowledge)
                    extracted.append(knowledge)
        
        return extracted
    
    def _extract_topic_from_question(self, question: str) -> str | None:
        """Extract main topic from a question."""
        question_lower = question.lower()
        
        topic_keywords = {
            "testing": ["test", "testing", "pytest", "jest", "unittest"],
            "database": ["database", "sql", "query", "table", "schema"],
            "api": ["api", "endpoint", "route", "request", "response"],
            "authentication": ["auth", "login", "password", "token", "session"],
            "deployment": ["deploy", "docker", "container", "server", "hosting"],
            "configuration": ["config", "setting", "environment", "env"],
            "performance": ["performance", "speed", "optimize", "fast", "slow"],
            "error_handling": ["error", "exception", "bug", "issue", "problem"],
        }
        
        for topic, keywords in topic_keywords.items():
            if any(kw in question_lower for kw in keywords):
                return topic
        
        return "general"
    
    async def _extract_from_errors(self, session_id: str, messages: list[dict]) -> list[dict]:
        """Extract knowledge from error handling patterns."""
        extracted = []
        
        for i, msg in enumerate(messages):
            content = msg.get("content", "") or ""
            
            # Look for error messages in tool results
            if msg["role"] == "tool" and any(word in content.lower() for word in ["error", "exception", "traceback", "failed"]):
                # Find the preceding tool call
                if i > 0:
                    prev_msg = messages[i - 1]
                    if prev_msg.get("tool_calls"):
                        try:
                            tool_calls = json.loads(prev_msg["tool_calls"])
                            for tc in tool_calls:
                                if isinstance(tc, dict):
                                    func = tc.get("function", {})
                                    tool_name = func.get("name", "")
                                    args_str = func.get("arguments", "{}")
                                    try:
                                        args = json.loads(args_str)
                                    except json.JSONDecodeError:
                                        args = {}
                                    
                                    # Extract error context
                                    error_knowledge = {
                                        "entry_type": "pattern",
                                        "scope": "project",
                                        "content": f"Error encountered with {tool_name}: {content[:300]}",
                                        "tags": ["error", "debugging", tool_name],
                                        "confidence": 0.7,
                                        "source_session_id": session_id,
                                    }
                                    await self._create_knowledge_if_new(**error_knowledge)
                                    extracted.append(error_knowledge)
                        except (json.JSONDecodeError, TypeError):
                            pass
        
        return extracted
    
    async def _extract_from_file_operations(self, session_id: str, messages: list[dict]) -> list[dict]:
        """Extract knowledge from file operations."""
        extracted = []
        
        # Collect all file paths mentioned
        all_files = set()
        for msg in messages:
            content = msg.get("content", "") or ""
            all_files.update(self._extract_file_paths(content))
        
        # Analyze file structure
        if all_files:
            # Group by directory
            dirs = {}
            for f in all_files:
                parts = f.split("/")
                if len(parts) > 1:
                    dir_path = "/".join(parts[:-1])
                    if dir_path not in dirs:
                        dirs[dir_path] = []
                    dirs[dir_path].append(f)
            
            # Create knowledge about project structure
            for dir_path, files in dirs.items():
                if len(files) >= 1:
                    knowledge = {
                        "entry_type": "convention",
                        "scope": "project",
                        "content": f"Project structure: {dir_path}/ contains {', '.join(Path(f).name for f in files[:5])}",
                        "tags": ["project_structure", "organization"],
                        "confidence": 0.6,
                        "source_session_id": session_id,
                    }
                    await self._create_knowledge_if_new(**knowledge)
                    extracted.append(knowledge)
        
        return extracted
    
    async def _detect_repetitive_patterns(self, session_id: str, messages: list[dict]) -> list[dict]:
        """Detect repetitive tool sequences for auto-creation."""
        extracted = []
        
        # Extract tool call sequences
        tool_sequences = []
        current_sequence = []
        
        for msg in messages:
            if msg.get("tool_calls"):
                try:
                    tool_calls = json.loads(msg["tool_calls"])
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            func = tc.get("function", {})
                            tool_name = func.get("name", "")
                            if tool_name:
                                current_sequence.append(tool_name)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif current_sequence:
                if len(current_sequence) >= 2:
                    tool_sequences.append(tuple(current_sequence))
                current_sequence = []
        
        if current_sequence and len(current_sequence) >= 2:
            tool_sequences.append(tuple(current_sequence))
        
        # Find repeated sequences
        sequence_counts = Counter(tool_sequences)
        
        for sequence, count in sequence_counts.items():
            if count >= 3:  # Same sequence 3+ times
                # This is a repetitive pattern - store it
                pattern_content = f"Repetitive workflow ({count} times): {' → '.join(sequence)}"
                
                knowledge = {
                    "entry_type": "pattern",
                    "scope": "project",
                    "content": pattern_content,
                    "tags": ["repetitive_workflow", "auto_create_candidate"],
                    "confidence": min(0.5 + (count * 0.1), 0.9),
                    "source_session_id": session_id,
                }
                
                await self._create_knowledge_if_new(**knowledge)
                extracted.append(knowledge)
                
                # If count >= 5 and confidence high enough, suggest skill creation
                if count >= 5:
                    await self._suggest_skill_creation(sequence, count, session_id)
        
        return extracted
    
    async def _suggest_skill_creation(self, sequence: tuple, count: int, session_id: str):
        """Suggest creating a skill for a repetitive pattern."""
        try:
            from config import load_config
            config = load_config()
            
            if not config.agent.auto_create_skills:
                return
            
            # Check auto-creation count for this session
            existing_skills = await KnowledgeBase.search_knowledge(
                entry_type="skill_created",
                scope="project",
                min_confidence=0.5,
                limit=config.agent.max_auto_creations,
            )
            
            session_creations = [
                e for e in existing_skills
                if e.get("metadata", {}).get("session_id") == session_id
            ]
            
            if len(session_creations) >= config.agent.max_auto_creations:
                log.info("Auto-creation limit reached for session %s", session_id)
                return
            
            # Generate skill name and description
            skill_name = f"auto-{sequence[0]}-workflow"
            skill_description = f"Automated workflow for {' → '.join(sequence[:3])} pattern"
            
            # Create the skill content
            skill_content = f"""# Auto-Generated Workflow

This skill was automatically created based on a repetitive pattern detected in your sessions.

## Pattern

The following tool sequence was repeated {count} times:
{chr(10).join(f"{i+1}. `{tool}`" for i, tool in enumerate(sequence))}

## Usage

This workflow is now available as a skill. The agent will use this pattern when it detects similar tasks.

## Tools in Sequence

{chr(10).join(f"- **{tool}**: Part of the automated workflow" for tool in sequence)}
"""
            
            # Create the skill
            from tools.create_skill import execute as create_skill_execute
            await create_skill_execute(
                name=skill_name,
                description=skill_description,
                content=skill_content,
                tags=["auto_created", "repetitive_pattern"],
                session_id=session_id,
            )
            
            log.info("Auto-created skill '%s' for repetitive pattern", skill_name)
            
        except Exception as e:
            log.warning("Failed to suggest skill creation: %s", e)
    
    async def _create_knowledge_if_new(
        self,
        entry_type: str,
        scope: str,
        content: str,
        source_session_id: str,
        tags: list[str] = None,
        confidence: float = 0.7,
        scope_identifier: str = None,
    ):
        """Create a knowledge entry if similar content doesn't exist."""
        try:
            # In-memory dedup for same session
            content_hash = hashlib.md5(content.lower()[:200].encode()).hexdigest()
            if content_hash in self._session_content_hashes:
                return
            self._session_content_hashes.add(content_hash)
            
            # Check if similar content exists (simple dedup)
            existing = await KnowledgeBase.search_knowledge(
                entry_type=entry_type,
                scope=scope,
                min_confidence=0.5,
                limit=10,
            )
            
            # Simple dedup: check if content is too similar to existing
            content_lower = content.lower()[:200]
            for entry in existing:
                existing_content = (entry.get("content", "") or "").lower()[:200]
                # If >80% overlap, skip
                if self._content_overlap(content_lower, existing_content) > 0.8:
                    return
            
            # Create the knowledge entry
            entry_id = await KnowledgeBase.create_knowledge_entry(
                entry_type=entry_type,
                scope=scope,
                scope_identifier=scope_identifier,
                content=content,
                source_session_id=source_session_id,
                confidence=confidence,
                tags=tags,
            )
            
            # Generate embedding in background (non-blocking)
            if entry_id:
                try:
                    from embeddings import get_embedding_manager
                    manager = get_embedding_manager()
                    # Don't await - let it run in background
                    asyncio.create_task(manager.generate_and_store_embedding(entry_id, content))
                except Exception as e:
                    log.debug("Embedding generation skipped: %s", e)
            
        except Exception as e:
            log.warning("Failed to create knowledge entry: %s", e)
    
    def _content_overlap(self, a: str, b: str) -> float:
        """Simple content overlap calculation."""
        if not a or not b:
            return 0.0
        
        words_a = set(a.split())
        words_b = set(b.split())
        
        if not words_a or not words_b:
            return 0.0
        
        intersection = words_a & words_b
        union = words_a | words_b
        
        return len(intersection) / len(union) if union else 0.0


# Singleton instance
_session_hook: SessionHook | None = None


def get_session_hook(llm_client=None) -> SessionHook:
    """Get or create the session hook singleton."""
    global _session_hook
    if _session_hook is None:
        _session_hook = SessionHook(llm_client)
    return _session_hook
