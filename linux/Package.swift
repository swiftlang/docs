// swift-tools-version: 6.2

import PackageDescription

let package = Package(
  name: "SwiftLinux",
  products: [
    .library(name: "SwiftLinux", targets: ["SwiftLinux"])
  ],
  dependencies: [
    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0")
  ],
  targets: [
    .target(
      name: "SwiftLinux",
      path: "Sources"
    )
  ]
)
