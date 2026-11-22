## ADDED Requirements

### Requirement: Reviewers push back on excess tests and specs
The managed `tests` reviewer SHALL report excess tests as well as missing ones. A test in the change is excess when:
- it checks the same behaviour as an existing test;
- it differs from other tests only in input or expected value, so they could be one table-driven or parametrized test;
- it depends on internals, so a refactor that keeps the behaviour would make it fail;
- it covers behaviour that neither the change's intent nor its spec scenarios ask for.

An excess-test finding SHALL point at the excess test and, for a duplicate, name the existing test it repeats. Its proposed fix SHALL be to delete, merge or extend tests, never to add one. The reviewer's instructions SHALL name no programming language or test framework.

When the diff changes files under `openspec/changes/`, the managed `rules` reviewer SHALL also check those files against the `rules:` in the repo's `openspec/config.yaml`, citing the rule like any other written rule.

#### Scenario: Duplicate test reported
- **WHEN** a change adds a test that asserts the same behaviour as an existing test in the repo
- **THEN** the tests reviewer reports it, naming the existing test, and proposes removing or merging the new one

#### Scenario: Openspec artifact checked against config rules
- **WHEN** the diff adds a spec delta that restates an existing requirement, and `openspec/config.yaml` has a rule asking to modify existing requirements instead
- **THEN** the rules reviewer reports the delta and cites that rule
