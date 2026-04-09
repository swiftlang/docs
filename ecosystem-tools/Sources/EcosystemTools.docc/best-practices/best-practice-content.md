# Contribute a Swift best practice

Create clear guidance with examples for best practice documentation.

## Overview

A best practice article provides practical advice from experienced Swift practitioners, solving common problems, or providing support to others doing the same.
These articles use a specific naming scheme to enable consistent URLs when they're published, so that those URLs can be referenced, or shared, from a variety of places.

### Create a guidance article

Use the [template provided in the Ecosystem docs repository](https://github.com/swiftlang/docs/blob/main/ecosystem-tools/templates/0000-best-practice-template.md) to create guidance.
Use a numbered filename to contain the content.
Prefix guidelines from the ecosystem group with `BSP-` and using the next number available.
When published with DocC, the filename of the article becomes a component in the URL and provides a consistent reference point.
Don't rename an existing article, or re-use a number from other guides that have been merged.

### Create an a title and abstract for the article

Provide a title and abstract that provides a high level, skimmable overview of the guidance.
The title should by a high level summary, and the abstract should work with the title to provide additional detail on where or how the guidance applies.

### Make an overview

Add an explicit `## Overview` section to provide an a concise introduction that cuts to the heart of the guidance and why it's important. The overview should:
- Identify the problem this guide solves, or what issues it avoids.
- Provide a concise summary (one to three sentences, not a paragraph or more) of why the problem is an issue and what benefits you get following the guidance.
- Identify the scope of the guidance: how or where it pertains, if the guidance isn't generally applicable.

### Provide an example of the guidance

Add a section header with an imperative title that provides the guidance.
Prefix the content with a green sphere (🟢), which uses a stop-light metaphor, so the reader can easily visually identify the preferred guidance.

Provide code examples that illustrate the guidance, using DocC's snippets feature to ensure that the example compiles:

  - For anything other than trivial code examples, create and use a snippet Swift file to verify that code samples compile.
  - To use a snippet, create a Swift file in the Snippets directory and reference the snippet in this template.
  - Reference the snippet in the best practice using the @Snippet directive. For example, if you create a snippet file named `BSP-0005.swift` in the Snippets directory, use the directive reference: `@Snippet(path: "EcosystemTools/Snippets/BSP-0005")`

### Share alternatives considered

Add the section `### Alternatives Considered` for a section to highlight lessor guidance.
For example, to illustrate patterns that work but aren't ideal, or express anti-patterns to avoid.
Any additional content in this section should provide detail as to why the pattern is suboptimal or one to avoid.

  - Use a yellow sphere  (🟠) to highlight suboptimal advice or patterns that may be required, but is not as good a solution as the primary guidance.

  - Use a red sphere (🛑) to highlight antipatterns or specifics to avoid.

### Identify the recency and applicability of the article

At that bottom of the document, include a "History" section to share relevant to specific versions of swift, and recen
cy of the guidance.
The following example shows a history block last updated in March, 2026 and with content relevant to Swift 6.0 or earlier:

```
### History

| Last updated | Swift Versions |
| ---- | ---- |
| March 2026 | Swift >= 6.0+ |
```