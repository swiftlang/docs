# ``SwiftApplePlatforms``

Build and ship apps for iOS, iPadOS, macOS, watchOS, tvOS, and visionOS.

@Metadata {
    @DisplayName("Apple platforms")
    @TitleHeading("Platforms")
}

## Overview

Swift is the primary language for building apps across Apple's platforms, with
deep integration into Xcode, Apple's SDKs, and frameworks like SwiftUI and
UIKit.

Visit [Apple Developer Documentation](https://developer.apple.com/documentation/technologies)
for platform-specific APIs, frameworks, and guides.

### Platform support

Apple platforms are a **Tier 1** platform under
[SP-0001, Swift Platform Support Tiers](https://github.com/swiftlang/swift-evolution/blob/main/policies/0001-platform-support-tiers.md).
macOS is a toolchain host — you develop with Swift on macOS and deploy to
macOS itself, or to iOS, watchOS, and tvOS.

| Platform | Deployment | Minimum version |
|---|---|---|
| macOS | Development and deployment | 10.13 |
| iOS | Deployment only | 11.0 |
| watchOS | Deployment only | 4.0 |
| tvOS | Deployment only | 11.0 |

On macOS, Swift Package Manager, SourceKit-LSP, the LLDB debugger, and the
Swift REPL are all available. On iOS, watchOS, and tvOS, only the debugger
applies, since apps for those platforms are built on macOS rather than on
the device itself.

Apple Inc. owns support for Apple platforms and requires pull request
testing to pass before merging changes that affect them. See
[Install Swift on macOS](https://www.swift.org/getting-started/#on-macos) to
get started, or the full
[platform support matrix](https://www.swift.org/platform-support/) for how
Apple platforms compare with every other platform Swift supports.
