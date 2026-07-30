// swift-tools-version: 6.2

import PackageDescription

let package = Package(
  name: "OfficialLibraries",
  products: [
    .library(name: "OfficialLibraries", targets: ["OfficialLibraries"])
  ],
  dependencies: [
    .package(url: "https://github.com/swiftlang/swift-docc-plugin", from: "1.1.0")
  ],
  targets: [
    .target(
      name: "OfficialLibraries",
      path: "Sources"
    )
  ]
)
