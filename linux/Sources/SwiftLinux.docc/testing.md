# Testing Swift packages on Linux

Run your tests, sanitizers, and code coverage on Linux while you develop on macOS.

## Overview

If you deploy libraries, tools, and services to Linux, you want to test on Linux too.
This guide shows how to run tests in a Linux container, find problems with sanitizers, measure code coverage, and load test fixtures.

## Run tests in a Linux container

On macOS, use a container runtime to run `swift test` in a Linux container.
[Apple's container tool](https://github.com/apple/container) is one option.
For more about container images and platform options, see <doc:building>.

To use `container`, install it and start its services.
The `container` project documents the macOS and hardware it requires.

```bash
container system start
```

The first run downloads the Swift image, which can take a few minutes.
To avoid the wait, pull the image ahead of time:

```bash
container image pull swift:latest
```

> Note: These examples use `swift:latest`.
> To test against a specific Swift version, use a tag such as `swift:6.4`.
> The `latest` tag changes with each release.

The following command uses `container` to run `swift test` in a Linux container, starting from your package directory:

```bash
# Run tests with container
container run -c 2 -m 8g --rm \
  -v "$PWD:/code" -w /code \
  swift:latest swift test --scratch-path .build/linux
```

The command mounts the current directory at `/code` within the Linux container, builds the Swift package, and runs the tests.
The `--scratch-path` option keeps the Linux build artifacts separate from any you have locally.

With Docker, the same command omits the `-c` and `-m` options:

```bash
# Run tests with Docker
docker run --rm \
  -v "$PWD:/code" -w /code \
  swift:latest swift test --scratch-path .build/linux
```

### Run commands in a container shell

To run more than one command, start a shell in the container instead of running a single command:

```bash
container run -c 2 -m 8g --rm -it \
  -v "$PWD:/code" -w /code \
  swift:latest bash
```

The rest of this article labels commands that run at this shell prompt with `# In the container`.
Add `--scratch-path .build/linux` to `swift` commands, as in the earlier example, so Linux build output stays out of your macOS build.
Files you write in `/code`, such as `coverage.lcov`, appear in your package directory on your Mac.
With Docker, use `docker run` and omit the `-c` and `-m` options.

### Cache build output between runs

The bind mount that shares your package from macOS slows builds.
Each `--rm` run also discards its build output.
To reuse the output between runs, store it in a named volume.
The volume exists only inside the Linux container.
Your macOS builds don't use it, and your package's `.build` directory stays clean.

```bash
# Create a volume that keeps Linux build output between runs
container volume create swift-build-cache

# Run tests, and write build output to the volume
container run -c 2 -m 8g --rm \
  -v "$PWD:/code" -v swift-build-cache:/build -w /code \
  swift:latest swift test --scratch-path /build
```

The first run builds everything.
Later runs only rebuild what changed.
Docker supports the same approach with `docker volume create` and `docker run`.

Delete the cache volume with this command:

```bash
container volume rm swift-build-cache
```

### Select tests

List the tests in a package, then run a subset:

```bash
# In the container
# List every test
swift test list --scratch-path .build/linux

# Run tests whose names match a regular expression
swift test --filter MyLibraryTests --scratch-path .build/linux

# Skip tests whose names match a regular expression
swift test --skip SlowTests --scratch-path .build/linux
```

For all the command-line options, see the [Swift test documentation](https://docs.swift.org/latest/documentation/packagemanagerdocs/swifttest) in the [Swift Package Manager documentation](https://docs.swift.org/latest/documentation/packagemanagerdocs).

## Find problems with sanitizers

Sanitizers add runtime checks to your code and report problems that tests alone can miss.
Pass `--sanitize` to `swift test` to build and run your tests with a sanitizer.

A sanitizer report makes `swift test` exit with a nonzero status, even when the test output says every test passed.
When you use a sanitizer with `swift test`, check the exit code as well as the test summary.

### Detect data races with ThreadSanitizer

ThreadSanitizer finds data races in concurrent code:

```bash
# In the container
swift test --sanitize=thread --scratch-path .build/linux
```

When it detects a race, it prints a `WARNING: ThreadSanitizer: data race` report with the stack of each conflicting access.
Run ThreadSanitizer regularly on code that uses concurrency, such as servers.

### Detect memory problems with AddressSanitizer

AddressSanitizer finds memory errors such as use-after-free and buffer overflows.
On Linux, it also runs LeakSanitizer to find memory leaks:

```bash
# In the container
swift test --sanitize=address --scratch-path .build/linux
```

On Linux, this command fails for any package that has XCTest tests.
LeakSanitizer reports leaks inside XCTest itself, and `swift test` exits with status `1` before it runs your tests.
Use one of these workarounds:

```bash
# In the container
# Keep the memory-error checks and turn off leak detection
ASAN_OPTIONS=detect_leaks=0 swift test --sanitize=address --scratch-path .build/linux

# Run only Swift Testing tests, and keep leak detection
swift test --sanitize=address --disable-xctest --scratch-path .build/linux
```

In a container shell, set the variable on the command line, as shown.
If you run a single `container run` or `docker run` command instead, add `-e ASAN_OPTIONS=detect_leaks=0` to it.

## Measure code coverage

Coverage data comes from Linux binaries, so run the commands in this section in a [container shell](<doc:testing#Run-commands-in-a-container-shell>).

Turn on code coverage reporting with the `--enable-code-coverage` option:

```bash
# In the container
swift test --enable-code-coverage --scratch-path .build/linux
```

Swift Package Manager writes raw profile data, a merged `default.profdata` file, and a JSON summary into a `codecov` directory under your build directory.
To print the path to the JSON summary, run `swift test --show-coverage-path`.

To produce a readable report, run `llvm-cov` from the Swift toolchain against the merged profile data and your test binary.

The test binary you pass to `llvm-cov` depends on Swift's build system.
Swift 6.4 changed the default from the native build system to Swift Build.
Swift Build produces a shared library for each test target.
The native build system produces an `.xctest` binary for the package.
Swift Package Manager merges the profile data in both cases.

The paths below assume `--scratch-path .build/linux`, as in the earlier command.
If you use a different scratch path, replace `.build/linux` with it.
If you [cache build output in a volume](<doc:testing#Cache-build-output-between-runs>), add `-v swift-build-cache:/build` to the shell command.
Then replace `.build/linux` with `/build` in the paths below.

### Report coverage in Swift 6.4

Point `llvm-cov` at your test target's shared library.
Replace `MyLibraryTests` with the name of your test target:

```bash
# In the container
llvm-cov report .build/linux/debug/MyLibraryTests.so \
  -instr-profile=.build/linux/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/'
```

If your package has more than one test target, add each additional shared library with another `-object` argument.

### Report coverage in Swift 6.2 and 6.3

Point `llvm-cov` at the test bundle, which Swift names after your package.
Replace `MyLibraryPackageTests` with your package's name followed by `PackageTests`:

```bash
# In the container
llvm-cov report .build/linux/debug/MyLibraryPackageTests.xctest \
  -instr-profile=.build/linux/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/'
```

### Export an lcov file for reporting coverage

The `lcov` format is a plain-text record of which lines your tests ran.
Some CI services can use it to show per-line coverage and annotate pull requests.
To produce one, run `llvm-cov export` with the same arguments as `llvm-cov report`.
Add `-format=lcov`, and redirect the output to a file.

In Swift 6.4 and later, export from your test target's shared library:

```bash
# In the container
llvm-cov export -format=lcov .build/linux/debug/MyLibraryTests.so \
  -instr-profile=.build/linux/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/' > coverage.lcov
```

In Swift 6.2 and 6.3, export from the test bundle:

```bash
# In the container
llvm-cov export -format=lcov .build/linux/debug/MyLibraryPackageTests.xctest \
  -instr-profile=.build/linux/debug/codecov/default.profdata \
  --ignore-filename-regex='\.build|Tests/' > coverage.lcov
```

## Load test fixtures

To give tests access to data files, declare them as Swift package resources on the test target:

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

> Note: In Swift 6.2, a test target that copies a resource directory can fail to build when the container mounts your package from macOS.
> The build stops with a file-copy error.
> Copy the package into the container and build there, or use Swift 6.3 or later.

## Next steps

To learn more, see:

- [Swift Testing](https://swiftpackageindex.com/apple/swift-testing/main/documentation/testing)
- [Testing in Vapor](https://docs.vapor.codes/advanced/testing/)
- [Hummingbird documentation](https://docs.hummingbird.codes)

To build and package your project for Linux, see <doc:building> and <doc:packaging>.
