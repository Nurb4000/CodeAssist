---
name: optimize
description: Optimize code for better performance, efficiency, or resource usage
slash: optimize
---

# Performance Optimization Skill

Analyze and optimize code for better performance.

## Steps

1. **Profile the code** - Identify bottlenecks
2. **Analyze time complexity** - Big O of current implementation
3. **Identify optimization opportunities:**
   - Algorithm improvements
   - Caching/memoization
   - Reduce allocations
   - Batch operations
   - Lazy evaluation
4. **Apply optimizations** carefully
5. **Verify correctness** is maintained

## Optimization Checklist

- [ ] Is there an O(n²) that could be O(n)?
- [ ] Can repeated computations be cached?
- [ ] Are data structures optimal for access patterns?
- [ ] Can loops be vectorized?
- [ ] Is memory being freed properly?

## Common Optimizations

| Problem | Solution |
|---------|----------|
| Repeated work | Memoization/caching |
| Slow lookups | Use hash maps |
| String building | Join instead of concatenate |
| Repeated I/O | Batch operations |
| Unnecessary work | Lazy evaluation |

## Guidelines

- Measure before optimizing
- Optimize hot paths first
- Don't sacrifice readability for tiny gains
- Document optimization decisions
- Consider memory vs speed tradeoffs
