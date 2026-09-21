---
name: optimize
description: Performance profiling and optimization workflow
slash: optimize
---

# Optimize Skill

Use this skill when the user wants to improve performance. Follow this data-driven optimization process.

## Steps

1. **Define the problem**
   - Ask the user what specifically is slow (response time, memory, CPU, startup)
   - Use `question` tool to clarify performance targets
   - Establish baseline measurements

2. **Profile and measure**
   - Use `shell` to run profiling tools:
     - Python: `python -m cProfile -s cumulative <script>`, `py-spy`, `memory_profiler`
     - Node: `node --prof <script>`, `clinic.js`
     - Go: `go test -bench=. -benchmem`, `pprof`
   - Use `test_runner` with timing to measure current performance
   - Record baseline metrics

3. **Identify bottlenecks**
   - Analyze profiling output for hot paths
   - Use `symbol_search` and `grep` to trace slow code paths
   - Look for common issues:
     - N+1 queries or API calls
     - Unnecessary loops or iterations
     - Memory allocations in hot paths
     - Missing caching opportunities
     - Blocking I/O in async code

4. **Plan optimizations**
   - List optimizations ranked by expected impact
   - Use `question` tool to confirm the plan with the user
   - Consider trade-offs (readability vs performance)

5. **Apply optimizations**
   - Use `diff_preview` before each change
   - Apply one optimization at a time
   - Measure after each change to verify improvement

6. **Verify results**
   - Run the full test suite to ensure correctness
   - Compare final metrics against baseline
   - Document the improvements

## Optimization Principles

- Measure first — don't guess where the bottleneck is
- Optimize the hot path, not the cold path
- One change at a time — verify each optimization independently
- If an optimization hurts readability, document why it's necessary
- Consider caching before algorithmic changes
