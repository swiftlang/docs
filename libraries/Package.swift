// swift-tools-version: 6.2

import PackageDescription

let package = Package(
  name: "Libraries",
  products: [
    .library(name: "Libraries", targets: ["Libraries"])
  ],
  dependencies: [
    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0")
  ],
  targets: [
    .target(
      name: "Libraries",
      path: "Sources"
    )
  ]
)
