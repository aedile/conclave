# PR Creation Checklist - MANDATORY FOR EVERY TASK

## Before Creating PR - Verify:
- [ ] All tests passing
- [ ] Coverage ≥ 90%
- [ ] `ruff check` passing
- [ ] `ruff format --check` passing
- [ ] `mypy` passing
- [ ] `bandit` passing
- [ ] `pre-commit run --all-files` passing

## PR Description - MUST Include ALL Sections:

### 1. Task Summary
```
**Task ID**: P#-T##
**Phase**: # (Name)
**Type**: TDD/Feature/Fix
```

### 2. Changes Made
- ✅ Bullet list of ALL changes
- ✅ ALL new files created
- ✅ ALL modified files

### 3. Acceptance Criteria Met
- [x] Checkbox for EACH criterion from backlog
- [x] All criteria must be checked

### 4. Testing
```bash
$ poetry run pytest <test_file> -v
========================== X passed in X.XXs ==========================

$ poetry run pytest --cov=<module>
Coverage: XX%
```

### 5. Quality Gates
- ✅ `ruff check` - Status
- ✅ `ruff format --check` - Status
- ✅ `mypy` - Status
- ✅ `bandit` - Status
- ✅ `pre-commit run --all-files` - Status

### 6. Self-Review
**QA Review ✅**
- Specific items reviewed

**DevOps Review ✅**
- Deployment considerations

### 7. Constitution Compliance
- **Priority 0 (Security)**: ✅ Status
- **Priority 1 (Quality Gates)**: ✅ Status
- **Priority 3 (TDD)**: ✅ Status
- **Priority 4 (Testing)**: ✅ Status
- **Priority 5 (Code Quality)**: ✅ Status

### 8. Backlog Updates
**BACKLOG: P#-T## complete ✅**

## PR Command Template:
```bash
gh pr create --base main --title "type: description (P#-T##)" --body "<full description from above>"
```

## NEVER:
- ❌ Create minimal PR descriptions
- ❌ Skip any section above
- ❌ Target feature branches (always main)
- ❌ Skip quality gate verification
