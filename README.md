# Welcome to the Swift Project Documentation

This repository hosts multiple [DocC][docc] catalogs that provide the source location for documentation content for the Swift project, hosted at [www.swift.org][www.swift.org].
The repository provides a location to collect and collaborate on Documentation for the Swift open source project.
For more information on the intent and inception, read the [Swift Docs Proposal][docs-proposal].

Contributions to the Swift Documentation are welcomed and encouraged!

To be a truly great community, Swift Documentation welcomes developers from all walks of life, with different backgrounds, and with a wide range of experience.
A diverse and friendly community has more great ideas, more unique perspectives, and produces more great code. 
We work diligently to make the Swift Documentation community welcoming to everyone.

To give clarity of what is expected of our members, Swift has adopted the code of conduct defined by the Contributor Covenant.
This document is used across many open source communities, and we think it articulates our values well.
For more, see the [Code of Conduct][conduct].

## What's in this repository

The top levels of this repository generally host a very light Swift package that wraps a documentation catalog.
This repository isn't meant for consumable libraries, any documentation for a Swift library should be maintained with that library.
This library hosts general content related to the Swift language, guides, and cross-cutting details that support the Swift ecosystem more broadly.
Each of the catalogs is matched with an entry in the [CODEOWNERS][codeowners] file, which provides the technical reviewers for that catalog.

Each directory has it's own Swift package in order to support a full breadth of tooling for documentation and examples, including snippets. 
The packages in this repository aren't meant to be depended upon or provide library. 

## How you can help

- [Report issues with existing content][issues]
- [Report issues with missing content or request content][issues]
- Fix typos
- Propose new content

See [contributing][contributing] for more information on proposing new content, style and content guidelines, and the details of how contributors add content to the Swift Documentation.

## License

This project is licensed under the terms of the [LICENSE][license]

[codeowners]: .github/CODEOWNERS
[www.swift.org]: https://www.swift.org/
[docs-proposal]: https://github.com/swiftlang/swift-org-website/blob/main/_info-architecture/0003-swift-docs-proposal.md
[issues]: https://github.com/swiftlang/docs/issues
[conduct]: https://www.swift.org/code-of-conduct
[contributing]: /CONTRIBUTING.md
[license]: /LICENSE.txt
[docc]: https://www.swift.org/documentation/docc/
