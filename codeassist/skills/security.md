---
name: security
description: Audit code for security vulnerabilities and apply security best practices
slash: security
---

# Security Audit Skill

Analyze code for security vulnerabilities and recommend fixes.

## Steps

1. **Read the code** - Understand the attack surface
2. **Check for common vulnerabilities:**
   - SQL injection
   - XSS (Cross-Site Scripting)
   - Command injection
   - Path traversal
   - Insecure deserialization
   - Hardcoded secrets
   - Weak cryptography
   - Improper input validation
3. **Rate severity** - Critical, High, Medium, Low
4. **Provide fixes** with specific code changes

## Vulnerability Checklist

- [ ] User input validated and sanitized
- [ ] SQL queries use parameterized statements
- [ ] No shell commands built from user input
- [ ] File paths validated against traversal
- [ ] Secrets not hardcoded in source
- [ ] Sensitive data not logged
- [ ] Dependencies have no known CVEs
- [ ] Authentication/authorization checks present

## OWASP Top 10

1. Injection flaws
2. Broken authentication
3. Sensitive data exposure
4. XML external entities
5. Broken access control
6. Security misconfiguration
7. XSS
8. Insecure deserialization
9. Using components with vulnerabilities
10. Insufficient logging

## Output Format

```
## Security Audit Report

### Critical (Immediate Action)
- [file:line] Vulnerability type and description

### High
- [file:line] Description

### Medium
- [file:line] Description

### Recommendations
General security improvements
```
