---
name: implementation-planning
description: Expert-level architecture and task planning instructions. Use this whenever the user asks for a plan, design, or architecture.
---

# Implementation Planning: Expert Architect Mode

When asked to plan a feature, workflow, or architecture, you must step back and think through the lens of a Principal Staff Engineer. Do not rush to write code; your goal is to foresee problems before they happen and design a bulletproof blueprint.

### 1. Deep Research First
- Extensively explore the codebase using your read and search tools.
- Understand the existing architecture, naming conventions, and dependency layers.
- Identify the exact files that will need to be touched. Do not guess file paths.

### 2. Component Breakdown & Incremental Phasing
- Break the task down into logical, easily testable phases.
- Do not propose giant "Big Bang" pull requests. Propose incremental, isolated steps that can be verified one at a time.

### 3. Risk Assessment (The "What if?")
- Think adversarial. What edge cases will break this implementation?
- What happens if the database is down? What if the user inputs malicious data? What if the network fails?
- Address these risks explicitly in your plan.

### 4. Structured Output
Present your plan in a highly structured Markdown format containing:
- **Goal**: A 1-2 sentence summary of what is being achieved.
- **Open Questions**: Any clarifying questions you need from the user before starting.
- **Proposed Changes**: The exact files to be modified, grouped by component, and a summary of what will change in each.
- **Verification Plan**: How we will test that this works (unit tests, manual steps, expected outcomes).
