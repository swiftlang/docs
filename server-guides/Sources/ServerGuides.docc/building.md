# Building Swift server applications

Assemble your server applications using Swift Package Manager.

## Overview

Use [Swift Package Manager](https://www.swift.org/documentation/package-manager/) to build server applications.
It provides a cross-platform foundation for building Swift code.
You can build using the command line or through an integrated development environment such as Xcode or Visual Studio Code.

## Choose a build configuration

Swift Package Manager supports two distinct build configurations, each optimized for different stages of your development workflow.
The configurations are `debug`, which you use during active development, and `release`, which you use when profiling or creating production artifacts.

### Use debug builds during development

When you run `swift build` without additional flags, Swift Package Manager creates a debug build:

```bash
swift build
```

Debug builds include full debugging symbols and runtime safety checks, which are essential during active development.
The compiler skips most optimizations to keep compilation times fast and preserve maximum debugging information.
This lets you quickly test changes and debug your app using `lldb` and breakpoints.
However, skipping optimizations can come at a significant cost to runtime performance.
Debug builds typically run more slowly than their release counterparts.

### Create release builds for production

For production deployments, use the release configuration by adding the `-c release` flag:

```bash
swift build -c release
```

The release configuration turns on all compiler optimizations.
The trade-off is longer compilation times because the optimizer performs extensive analysis and transformations.
Release builds still include some debugging information for crash analysis, but omit development-only checks and assertions.

## Optimize your builds

Beyond choosing debug or release mode, several compiler flags can fine-tune your builds for specific scenarios.

### Build specific products

If your package defines multiple executables or library products, Swift Package Manager builds everything declared in the package by default.
Build only what you need using the `--product` flag:

```bash
swift build --product MyAPIServer
```

Building a specific product is useful in monorepo setups or packages with multiple deployment targets.
It avoids compiling tools or test utilities you don't need for a given deployment.

### Enable cross-module optimization

Swift supports cross-module optimization, which lets the compiler optimize code across module boundaries:

```bash
swift build -c release -Xswiftc -cross-module-optimization
```

The `-Xswiftc` flag passes options to the Swift compiler.
`-cross-module-optimization` tells the compiler to optimize across module boundaries.
By default, Swift optimizes each module in isolation.
Cross-module optimization removes this boundary, enabling techniques such as inlining function calls between modules.

For code that frequently calls small functions across module boundaries, this can yield meaningful performance improvements.
However, results vary by project because optimizations are specific to your code.
Always benchmark your specific workload with and without this flag before deploying to production.

### Choose static or dynamic linking for the standard library

By default, Swift build artifacts link the standard library dynamically.
This keeps individual build artifacts smaller, and multiple programs can share a single copy of the Swift runtime.
However, dynamic linking requires the Swift runtime to be installed on your deployment target.

For deployment scenarios where you want more self-contained build artifacts, statically link the Swift standard library:

```bash
swift build -c release --static-swift-stdlib
```

The resulting build artifacts still dynamically link to `glibc`, but have fewer other dependencies on the target system.
These executables bundle the Swift runtime directly:

| Aspect | Dynamic linking | Static linking |
|--------|----------------|----------------|
| Build artifact size | Smaller (runtime not included) | Larger (runtime included in binary) |
| Deployment complexity | Requires Swift runtime on target system | Self-contained, no runtime needed |
| Version management | Must match runtime version on system | Each artifact includes its own runtime version |

For deploying to VMs or bare metal where you don't control the system configuration, static linking removes the dependency on a pre-installed Swift runtime.

> Note: This technique doesn't apply on Apple platforms.

## Preserve debug information for symbolication

Release builds optimize for performance and don't embed DWARF debug information by default.
Without DWARF, a crashing binary's stack trace reports raw addresses instead of function names, files, and line numbers.
You have two ways to keep symbolication working: embed the debug information in the binary, or split it into a sidecar file you ship separately.

### Split debug information into a sidecar file

For a smaller deployable artifact, separate the debug information into its own file and strip the original binary.
This is the layout Linux distributions use for `-debuginfo` and `-dbgsym` packages.

```bash
cd .build/release

# Copy debug sections into a sidecar file.
objcopy --only-keep-debug MyServer MyServer.debug

# Remove debug information from the deployed binary.
objcopy --strip-debug --strip-unneeded MyServer

# Record a link from the stripped binary to its sidecar.
objcopy --add-gnu-debuglink=MyServer.debug MyServer
```

The Swift source repository has scripts to make debug sidecar files:

- [make-debuglink](https://github.com/swiftlang/swift/blob/main/test/Backtracing/Inputs/make-debuglink)
- [make-minidebug](https://github.com/swiftlang/swift/blob/main/test/Backtracing/Inputs/make-minidebug)

That you can use to create debug sidecars.
The Swift backtracer supports gzip, zstd, and lzma compressed data,
provided it can locate the relevant dynamic libraries at runtime.

You now have two artifacts: a small, stripped `MyServer` to ship in your container or distribution package, and a `MyServer.debug` to publish to a symbol server or ship in a companion debug-info package.
For how symbolicators consume the sidecar at crash time, see <doc:debugging-a-service-using-a-backtrace#Map-a-frame-to-source>.

### Verify build IDs match

A sidecar is only useful if it shares a build ID with the binary it describes.
The linker writes the build ID into a `.note.gnu.build-id` section that survives stripping, and symbolicators use it to pair the two files.

Read the build ID from each file:

```bash
readelf -n MyServer       | grep 'Build ID'
readelf -n MyServer.debug | grep 'Build ID'
```

Both commands print the same hexadecimal value when the files match.
A mismatch means the binary and sidecar came from different builds; in that case the sidecar can't symbolicate the binary and you need to rebuild both together.

## Build for another platform

Swift build artifacts are both platform- and architecture-specific.
Artifacts you create on macOS run only on macOS; those you create on Linux run only on Linux.
This creates a challenge when you work on macOS and deploy to Linux servers.

You can use Xcode for development, but it can't produce Linux artifacts for deployment.
Swift provides two main approaches for cross-platform building.

### Build with Linux containers

On macOS, you can use [Apple's container tool](https://github.com/apple/container) or the Docker CLI to verify your project builds under Linux.
Apple publishes official Swift Docker images to [Docker Hub](https://hub.docker.com/_/swift), which provide complete Linux build environments.

To build your application using the latest Swift release image:

```bash
# Build with container
container run -c 2 -m 8g --rm -it \
  -v "$PWD:/code" -w /code \
  swift:latest swift build

# Build with Docker
docker run --rm -it \
  -v "$PWD:/code" -w /code \
  swift:latest swift build
```

These commands mount your current directory as `/code` in the container, set it as the working directory, and run `swift build` inside the Linux environment.
The `swift:latest` container image provides this environment and `swift build` produces Linux-compatible build artifacts.

If you're on Apple silicon and need to target `x86_64` Linux servers, you need to specify the target platform with the `--platform` option:

```bash
# Build with container
container run -c 2 -m 8g --rm -it \
  -v "$PWD:/code" -w /code \
  --platform linux/amd64 \
  -e QEMU_CPU=max \
  swift:latest swift build

# Build with Docker
docker run --rm -it \
  -v "$PWD:/code" -w /code \
  --platform linux/amd64 \
  -e QEMU_CPU=max \
  swift:latest swift build
```

The `--platform` flag runs the container with QEMU emulation.
The `-e QEMU_CPU=max` environment variable enables the maximum set of CPU features within the emulated environment, giving your code access to the broadest instruction set the emulation supports.

To build your code into a container, you typically use a container declaration — a Dockerfile or Containerfile — that specifies all the steps to assemble the container image holding your build artifacts.
Container-based builds work well in CI/CD pipelines and for validating that your code builds cleanly on Linux.
However, Docker container builds can be slower than native builds, especially on Apple silicon where `x86_64` containers run through emulation.

For more information on packaging your application or service, see <doc:packaging>.

### Build with a VS Code Dev Container

Visual Studio Code supports the Dev Container feature that lets you open, build, and debug your project within a container running on your local machine.
The Dev Container builds your code in the Linux environment that the container provides.
For more information on using a Dev Container, read [Visual Studio Code Dev Containers](https://docs.swift.org/vscode/documentation/userdocs/remote-dev/).

### Cross-compile with the Static Linux SDK

If the performance overhead of Docker-based builds affects your workflow, Swift 5.9 and later provide Static Linux SDKs.
Find the link to install the static Linux SDK on the [install page at Swift.org](https://www.swift.org/install/), and detailed instructions in the article [Getting Started with the Static Linux SDK](https://www.swift.org/documentation/articles/static-linux-getting-started.html).
The SDK enables cross-compilation directly from macOS to Linux without using a container:

```bash
# Build for x86_64 Linux
swift build -c release --swift-sdk x86_64-swift-linux-musl

# Build for ARM64 Linux
swift build -c release --swift-sdk aarch64-swift-linux-musl
```

These SDK targets use musl libc (a lightweight C library) instead of glibc (the GNU C library) to produce statically linked build artifacts.
The resulting executables have minimal dependencies on the target Linux system, making them highly portable across Linux distributions.
However, the resulting executables are typically larger than dynamically linked equivalents.

Cross-compilation runs natively on your Mac's architecture without emulation, so it's faster than Docker-based builds.
The trade-off is build environment fidelity: you verify that your code cross-compiles to Linux, not that it builds on an actual Linux system.
Cross-compilation also limits you to the static libraries available in the SDK;
your code can't use `dlopen` or similar mechanisms to dynamically load libraries available on the target system.

For most projects, this distinction doesn't matter.
However, packages with complex C dependencies can behave differently when built natively on Linux versus cross-compiled.

## Locate your build artifacts

After compiling, locate your build artifacts.
Swift Package Manager places them in directories that vary by platform and architecture:

```bash
# Show where debug build artifacts are located
swift build --show-bin-path

# Show where release build artifacts are located
swift build --show-bin-path -c release
```

Swift Package Manager writes the build products to the scratch path, which defaults to `.build`, but the specific location can vary by platform or Swift compiler version.
Use the `--show-bin-path` flag in deployment scripts to locate the build product without hardcoding platform-specific paths.

If you're uncertain which platform a binary targets, use the `file` command to inspect it:

```
file .build/debug/MyServer
```

Output from a debug build on macOS with Apple silicon:

```
.build/debug/MyServer: Mach-O 64-bit executable arm64
```

Output from a debug build on Linux, built inside a container on Apple silicon:

```
.build/debug/MyServer: ELF 64-bit LSB pie executable,
  ARM aarch64, version 1 (SYSV), dynamically linked,
  interpreter /lib/ld-linux-aarch64.so.1, for GNU/Linux 3.7.0,
  BuildID[sha1]=ec68ac934b11eb7364fce53c95c42f5b83c3cb8d,
  with debug_info, not stripped
```

Output from a debug build on macOS, built using the container tool with `x86_64` emulation:

```
.build/debug/MyServer: ELF 64-bit LSB pie executable,
  x86-64, version 1 (SYSV), dynamically linked,
  interpreter /lib64/ld-linux-x86-64.so.2, for GNU/Linux 3.2.0,
  BuildID[sha1]=40357329617ac9629e934b94415ff4078681b45a,
  with debug_info, not stripped
```

Output from a debug build using the static Linux SDK (`swift build --swift-sdk x86_64-swift-linux-musl`):

```
.build/debug/MyServer: ELF 64-bit LSB executable,
  x86-64, version 1 (SYSV), statically linked,
  BuildID[sha1]=04ae4f872265b1e0d85ff821fd26fc102993b9f2,
  with debug_info, not stripped
```
