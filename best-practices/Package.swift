// swift-tools-version: 6.2

import PackageDescription

let package = Package(
  name: "BestPractices",
  dependencies: [
    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0")
  ],
  targets: [
    .target(
      name: "BestPractices",
      path: "Sources"
    )
  ]
)
