# Testing Swift packages on Linux

Run your tests, sanitizers, and code coverage on Linux while you develop on macOS.

## Overview

If you deploy libraries, tools, and services to Linux, you want to test on Linux too.
This guide shows how to run tests in a Linux container, find problems with sanitizers, measure code coverage, and load test fixtures.

## Run tests in a Linux container

On macOS, you can a container runtime to run `swift test` in a Linux container.
Use [Apple's container tool](https://github.com/apple/container) or the Docker CLI with an official Swift image.
For more about container images and platform options, see <doc:building>.

For example, the following command uses `container` to run `swift test` as you might in macOS from your local directory:

```bash
# Run tests with container
container run -c 2 -m 8g --rm \
  -v "$PWD:/code" -w /code \
  swift:latest swift test --scratch-path .build/linux
```

The command mounts the current directory at `/code`, builds it for Linux, and runs the tests.
The `--scratch-path` option keeps the Linux build artifacts separate from any you have locally.

### Select tests

List the tests in a package, then run a subset:

```bash
# List every test
swift test list

# Run tests whose names match a regular expression
swift test --filter MyLibraryTests

# Skip tests whose names match a regular expression
swift test --skip SlowTests
```

For all the command line options, see the [Swift test documentation](https://docs.swift.org/latest/documentation/packagemanagerdocs/swifttest) in the [Swift Package Manager documentation](https://docs.swift.org/latest/documentation/packagemanagerdocs).

## Find problems with sanitizers

Sanitizers add runtime checks to your code and report problems that tests alone can miss.
Pass `--sanitize` to `swift test` to build and run your tests with a sanitizer.

A sanitizer report makes `swift test` exit with a nonzero status, even when the test output says every test passed.
When you use a sanitizer with `swift test`, check the exit code as well as the test summary.

### Detect data races with ThreadSanitizer

Thread sanitizer finds data races in concurrent code:

```bash
swift test --sanitize=thread
```

When it detects a race, it prints a `WARNING: ThreadSanitizer: data race` report with the stack of each conflicting access.
Run ThreadSanitizer regularly on code that uses concurrency, such as servers.

### Detect memory problems with AddressSanitizer

AddressSanitizer finds memory errors such as use-after-free and buffer overflows.
On Linux, it also runs LeakSanitizer to find memory leaks:

```bash
swift test --sanitize=address
```

On Linux, this command fails for any package that has XCTest tests.
LeakSanitizer reports leaks inside XCTest itself, and `swift test` exits with status `1` before it runs your tests.
Use one of these workarounds:

```bash
# Keep the memory-error checks and turn off leak detection
ASAN_OPTIONS=detect_leaks=0 swift test --sanitize=address

# Run only Swift Testing tests, and keep leak detection
swift test --sanitize=address --disable-xctest
```

To pass the environment variable into a container, add `-e ASAN_OPTIONS=detect_leaks=0` to a `container run` or `docker run` command.

## Measure code coverage

To turn on code coverage, use the command-line option `--enable-code-coverage`:

```bash
swift test --enable-code-coverage
```

Swift Package Manager writes raw profile data, a merged `default.profdata` file, and a JSON summary into a `codecov` directory under your build directory.
To print the path to the JSON summary, run `swift test --show-coverage-path`.

To produce a readable report, run `llvm-cov` from the Swift toolchain against the merged profile data and your test binary.
Linux doesn't have `xcrun`, so run the tool directly.

Which test binary you pass depends on the build system.
Swift 6.4 changed the default build system from the native build system to Swift Build.
Swift Build produces a shared library for each test target.
The native build system produces an `.xctest` binary for the package.
Swift Package Manager merges the profile data in both cases.

If you pass `--scratch-path`, replace `.build` in the paths below with your scratch path.

### Report coverage in Swift 6.4

Point `llvm-cov` at your test target's shared library.
Replace `MyLibraryTests` with the name of your test target:

```bash
llvm-cov report .build/debug/MyLibraryTests.so \
  -instr-profile=.build/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/'
```

To export an `lcov` file for CI tools, use `llvm-cov export`:

```bash
llvm-cov export -format=lcov .build/debug/MyLibraryTests.so \
  -instr-profile=.build/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/' > coverage.lcov
```

If your package has more than one test target, add each additional shared library with another `-object` argument.

### Report coverage in Swift 6.2 and 6.3

Point `llvm-cov` at the test bundle, which Swift names after your package.
Replace `MyLibraryPackageTests` with your package's name followed by `PackageTests`:

```bash
llvm-cov report .build/debug/MyLibraryPackageTests.xctest \
  -instr-profile=.build/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/'
```

The `llvm-cov export` command takes the same arguments.

## Load test fixtures

To give tests access to data files, declare them as resources on the test target:

```swift
.testTarget(
    name: "MyLibraryTests",
    dependencies: ["MyLibrary"],
    resources: [.copy("Fixtures")]
)
```

Then read them with `Bundle.module`:

```swift
import Foundation
import Testing

@Test func loadsFixture() throws {
    let url = try #require(
        Bundle.module.url(
            forResource: "data",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    let text = try String(contentsOf: url, encoding: .utf8)
    #expect(text.contains("fixture"))
}
```

> Note: In Swift 6.2, a test target that copies a resource directory can fail to build when your package is on a macOS volume mounted into the container.
> The build stops with a file-copy error.
> Copy the package into the container and build there, or use Swift 6.3 or later.

## Next steps

To learn more about the frameworks, see:

- [Swift Testing](https://swiftpackageindex.com/apple/swift-testing/main/documentation/testing)
- [Testing in Vapor](https://docs.vapor.codes/advanced/testing/)
- [Hummingbird documentation](https://docs.hummingbird.codes)

To build and package your project for Linux, see <doc:building> and <doc:packaging>.
