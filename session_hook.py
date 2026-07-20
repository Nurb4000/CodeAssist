"""
CodeAssist Session Hook - Auto-generates session summaries and extracts knowledge.

Called when a session ends (WebSocket disconnect) to:
1. Generate a session summary
2. Extract knowledge entries
3. Log file snapshots
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from session import Session
from knowledge import KnowledgeBase

log = logging.getLogger(__name__)


class SessionHook:
    """Handles post-session processing for knowledge base population."""
    
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
    
    async def on_session_end(self, session: Session, agent=None):
        """Called when a session ends. Generates summary and extracts knowledge."""
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
            # Simple heuristic to find file paths
            for word in content.split():
                if "/" in word and (word.endswith(".py") or word.endswith(".js") or word.endswith(".ts")):
                    # Clean up the word
                    clean = word.strip('",.')
                    if "/" in clean and len(clean) < 200:
                        files_modified.add(clean)
        
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
            # Extract from tool calls
            for msg in messages:
                if msg.get("tool_calls"):
                    try:
                        tool_calls = json.loads(msg["tool_calls"])
                        for tc in tool_calls:
                            if isinstance(tc, dict):
                                func = tc.get("function", {})
                                tool_name = func.get("name", "")
                                args_str = func.get("arguments", "{}")
                                
                                try:
                                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                                except json.JSONDecodeError:
                                    continue
                                
                                # Extract knowledge from write/edit operations
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
                                
                                # Extract knowledge from shell commands
                                elif tool_name == "shell":
                                    command = args.get("command", "")
                                    if command:
                                        # Extract common patterns
                                        if "pytest" in command or "test" in command:
                                            await self._create_knowledge_if_new(
                                                entry_type="pattern",
                                                scope="project",
                                                content=f"Test command pattern: {command[:200]}",
                                                source_session_id=session_id,
                                                tags=["testing", "command"],
                                            )
                                        elif "git" in command:
                                            await self._create_knowledge_if_new(
                                                entry_type="pattern",
                                                scope="project",
                                                content=f"Git workflow pattern: {command[:200]}",
                                                source_session_id=session_id,
                                                tags=["git", "workflow"],
                                            )
                    except (json.JSONDecodeError, TypeError):
                        pass
            
            # Extract from assistant responses (look for patterns, conventions, decisions)
            for msg in messages:
                if msg["role"] == "assistant":
                    content = msg.get("content", "") or ""
                    if len(content) < 50:
                        continue
                    
                    # Look for pattern indicators
                    content_lower = content.lower()
                    
                    if any(phrase in content_lower for phrase in ["pattern", "convention", "should", "recommend"]):
                        # This might contain a pattern or convention
                        if len(content) > 100:
                            await self._create_knowledge_if_new(
                                entry_type="pattern",
                                scope="project",
                                content=content[:1000],
                                source_session_id=session_id,
                                tags=["pattern", "extracted"],
                            )
                    
                    # Look for decisions
                    if any(phrase in content_lower for phrase in ["decided", "chose", "selected", "will use"]):
                        if len(content) > 100:
                            await self._create_knowledge_if_new(
                                entry_type="decision",
                                scope="project",
                                content=content[:1000],
                                source_session_id=session_id,
                                tags=["decision", "extracted"],
                            )
            
            log.info("Knowledge extraction completed for session %s", session_id)
            
        except Exception as e:
            log.exception("Error extracting knowledge from session %s", session_id)
    
    async def _create_knowledge_if_new(
        self,
        entry_type: str,
        scope: str,
        content: str,
        source_session_id: str,
        tags: list[str] = None,
        confidence: float = 0.7,
    ):
        """Create a knowledge entry if similar content doesn't exist."""
        try:
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
            
            await KnowledgeBase.create_knowledge_entry(
                entry_type=entry_type,
                scope=scope,
                content=content,
                source_session_id=source_session_id,
                confidence=confidence,
                tags=tags,
            )
            
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
