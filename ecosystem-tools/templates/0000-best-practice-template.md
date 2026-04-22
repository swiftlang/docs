# (ESG-0000) best practice title

<!--
- Use H1 header for the title, leading with the prefix and number of the best practice, then provide a concise title describing the best practice.

- name the markdown file with a prefix and number (example ESG-0004) to ensure consistent URL presentation when merged into a DocC Catlog.
- Once approved, ***never rename***, only deprecate or remove.
-->

{{ Replace with a single sentence abstract that describes what the best practice covers and expands upon title. }}

## Overview

🟢 - Summarize the core guidance in a lead sentence. Keep the overview concise and to the point.

Extend the overview with an additional paragraph providing more background if needed, preferring to link to external specifications, documentation, etc as needed instead of providing it inline.

<!-- DEPRECATION:
- If a best practice is no longer valid
    - update the title to include (DEPRECATED)
    - add into the overview where to go for updated best practice that replaces the prior advice, and (if relevant) what versions of Swift are suggested where the updated/new advice applies.
    - Put the redirect in a WARNING aside at the top of the overview
- Move deprecated advice to the bottom of any curation/organization list of practices
-->

### Example

Provide a clear, complete, ideally "real" example of the best practice. If describing an API pattern, show both the implementation side and "call site" usage to illustrate it.

If there's supporting tasks for the best practice, include the specifics of how to enable them in further H3 sections. Write the H3 section headings as imperative verb phrases that describe what the example shows and what to do for the best practice.

<!-- Use Snippets to verify code examples:
- For anything other than trivial code examples, create and use a snippet Swift file to verify that code samples compile.
  - To use a snippet, create a Swift file in the Snippets directory and reference the snippet in this template.
  - Reference the snippet in the best practice using the @Snippet directive. For example, if you create a snippet file named `ESG-0005.swift` in the Snippets directory, use the directive reference:
  `@Snippet(path: "EcosystemTools/Snippets/ESG-0005")`
-->

## Alternatives Considered

Lead with a 🟠 yellow/orange stoplight emoji to indicate alternates with tradeoffs. Explain when/why you might need to fall back and not use the best practice.

Lead with a 🔴 red stoplight emoji to indicate an anti-pattern and what not to do. Explain why or the impact of not following the best practice.

## History

<!-- 
- include last updated (month/year), should be updated if the practice is edited
- Swift revision applicable - identify what versions of Swift this applies to as well
-->

| Last updated | Swift Versions |
| ---- | ---- |
| February 2026 | Swift >= 6.0 |
