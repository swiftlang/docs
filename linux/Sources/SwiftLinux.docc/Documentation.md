# ``SwiftLinux``

Build and host libraries, apps, and services on Linux.

@Metadata {
    @DisplayName("Linux")
    @TitleHeading("Platforms")
}

## Overview

Linux is a **Tier 1** platform under
[SP-0001, Swift Platform Support Tiers](https://github.com/swiftlang/swift-evolution/blob/main/policies/0001-platform-support-tiers.md).
The Swift project provides official toolchain builds, so you can both
develop and deploy Swift on Linux.

### Platform support

| Distribution | Minimum version |
|---|---|
| Ubuntu | 22.04 |
| Debian | 12 |
| Fedora | 41 |
| Amazon Linux | 2023 |
| Red Hat Universal Base Image | 9 |

Every supported distribution includes the same set of tools: Swift Package
Manager, SourceKit-LSP, the LLDB debugger, and the Swift REPL.

Apple Inc. owns support for Linux and requires pull request testing to pass
before merging changes that affect it. See
[Install Swift on Linux](https://www.swift.org/getting-started/#on-linux) to
get started, or the full
[platform support matrix](https://www.swift.org/platform-support/) for how
Linux compares with every other platform Swift supports.

## Topics

- <doc:ServerGuides>
