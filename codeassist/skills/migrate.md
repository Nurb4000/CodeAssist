---
name: migrate
description: Database migration and data transformation workflow
slash: migrate
---

# Migrate Skill

Use this skill when the user needs to perform database migrations or data transformations.

## Steps

1. **Understand the current schema**
   - Read the existing migration files or schema definitions
   - Use `grep` to find all database-related code
   - Use `symbol_search` to find model definitions and queries
   - Document the current schema state

2. **Plan the migration**
   - Describe the schema changes precisely
   - List all affected models, queries, and APIs
   - Plan a rollback strategy for each change
   - Use `question` tool to confirm the migration plan with the user

3. **Write the migration**
   - Create the migration file following the project's conventions
   - Include both forward (upgrade) and backward (downgrade) operations
   - Use `diff_preview` to show the migration before applying

4. **Test the migration**
   - Run the migration against a test database
   - Use `test_runner` to verify existing tests still pass
   - Test the rollback path
   - Verify data integrity after migration

5. **Apply the migration**
   - Apply to development first
   - Run the full test suite
   - Apply to staging/production with user confirmation

6. **Verify and document**
   - Verify the schema matches expectations
   - Update documentation if needed
   - Log the migration to the knowledge base

## Safety Rules

- ALWAYS backup data before running migrations
- ALWAYS test the rollback path
- NEVER apply migrations directly to production without testing
- If the migration involves data transformation, verify row counts before/after
- Use transactions when possible to ensure atomicity
