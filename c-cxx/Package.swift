// swift-tools-version: 6.2

import PackageDescription

let package = Package(
  name: "swift-c-cxx",
  products: [
    .library(name: "SwiftCCxx", targets: ["SwiftCCxx"])
  ],
  dependencies: [
    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0")
  ],
  targets: [
    .target(
      name: "SwiftCCxx",
      path: "Sources"
    )
  ]
)
