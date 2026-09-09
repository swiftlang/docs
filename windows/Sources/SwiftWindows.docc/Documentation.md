# ``SwiftWindows``

Build and run Swift on Windows.

@Metadata {
    @DisplayName("Windows")
    @TitleHeading("Platforms")
}

## Overview

Swift supports native development on Windows, including a downloadable
toolchain and integration with Visual Studio's build tools.

Visit [Install Swift on Windows](https://www.swift.org/install/windows/) to
get started. More guidance on building and porting packages to Windows is
coming soon.

### Platform support

Windows is a **Tier 1** platform under
[SP-0001, Swift Platform Support Tiers](https://github.com/swiftlang/swift-evolution/blob/main/policies/0001-platform-support-tiers.md),
and a toolchain host — the Swift compiler, Swift Package Manager, and
SourceKit-LSP all run natively on Windows 10.0 and later.

The LLDB debugger is available on Windows, but the Swift REPL isn't
currently supported there.

Apple Inc. owns support for Windows and requires pull request testing to
pass before merging changes that affect it. See
[Install Swift on Windows](https://www.swift.org/getting-started/#on-windows)
to get started, or the full
[platform support matrix](https://www.swift.org/platform-support/) for how
Windows compares with every other platform Swift supports.
