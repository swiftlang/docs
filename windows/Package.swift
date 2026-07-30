// swift-tools-version: 6.2

import PackageDescription

let package = Package(
  name: "SwiftWindows",
  products: [
    .library(name: "SwiftWindows", targets: ["SwiftWindows"])
  ],
  dependencies: [
    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0")
  ],
  targets: [
    .target(
      name: "SwiftWindows",
      path: "Sources"
    )
  ]
)
