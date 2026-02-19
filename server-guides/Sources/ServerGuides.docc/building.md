# Building Swift Server Applications

Assemble your server applications using Swift Package Manager

Use [Swift Package Manager](/documentation/package-manager/) to build server applications.
It provides a cross-platform foundation for building Swift code.
You can build using the command line or through an integrated development environment (IDE) such as Xcode or Visual Studio Code.

## Choose a build configuration

The Swift Package Manager supports two distinct build configurations, each optimized for different stages of your development workflow.
The configurations are `debug`, frequently used during development, and `release`, which you use when profiling or creating production artifacts.

### Use debug builds during development

When you run `swift build` without additional flags, Swift Package Manager creates a debug build:

```bash
swift build
```

These debug builds include full debugging symbols and runtime safety checks, which are essential during active development.
The compiler skips most optimizations to keep compilation times fast, letting you quickly test changes.
However, this can come at a significant cost to runtime performance. Debug builds typically run slower than their release counterparts.

### Create release builds for production

For production deployments, use the release configuration by adding the `-c release` flag:

```bash
swift build -c release
```

The release configuration turns on all compiler optimizations.
The trade-off is longer compilation times because the optimizer performs extensive analysis and transformations.
Release builds still include some debugging information for crash analysis, but omit development-only checks and assertions.

## Optimize your builds

After selecting your build configuration, you can apply additional optimizations.
Beyond choosing debug or release mode, several compiler flags can fine-tune your builds for specific scenarios.

### Preserve frame pointers

The compiler can omit frame pointers to gain additional performance.
Frame pointers are data structures that enable accurate stack traces.
Without them, debugging production crashes becomes more difficult because stack traces are less reliable.
For production server applications, preserving frame pointers is usually worth the minimal performance cost.
The compiler doesn't omit frame pointers by default in release configurations, but you can be explicit to preserve them:

```bash
swift build -c release -Xcc -fno-omit-frame-pointer
```

The `-Xcc` flag passes options to the C compiler.
Here, `-fno-omit-frame-pointer` [tells the compiler](https://clang.llvm.org/docs/ClangCommandLineReference.html#cmdoption-clang-fomit-frame-pointer) to preserve frame pointers.
This ensures that debugging tools can produce accurate backtraces, which are critical when you need to diagnose a crash or profile performance.
The performance impact is typically negligible for server workloads, while the debugging benefits are substantial.

### Enable cross-module optimization

Swift 5.2 added cross-module optimization, which lets the compiler optimize code across module boundaries:

```bash
swift build -c release -Xswiftc -cross-module-optimization
```

The `-Xswiftc` flag passes options to the Swift compiler.
Here, `-cross-module-optimization` tells the compiler to optimize code across module boundaries.
By default, Swift optimizes each module in isolation.
Cross-module optimization removes this boundary, enabling techniques such as inlining function calls between modules.

For code that makes frequent use of small functions across module boundaries, this can yield meaningful performance improvements.
However, results vary for every project because optimizations are specific to your code.
Always benchmark your specific workload with and without this flag before deploying to production.

### Build specific products

If your package defines multiple executables or library products, Swift Package Manager builds everything declared in the package by default.
You can build only what you need using the `--product` flag:

```bash
swift build --product MyAPIServer
```

This is particularly useful in monorepo (single-repository) setups or packages with multiple deployment targets, because it avoids compiling tools or test utilities you don't need for a particular deployment.

### Use package traits

Beyond additional compiler flags, Swift 6.1 introduces another way to control what gets built.
Starting with Swift 6.1, packages can define traits, which enable or disable optional features at build time.

With package traits, a package can define additional, optional code that you can enable for your service or library.
You can toggle experimental APIs, performance monitoring, or deployment settings without creating separate branches.
This can be much clearer than using preprocessor macros or toggling features using environment variables in package manifests.

For details on defining traits, conditional dependencies, trait unification, and advanced use cases, see [Package Traits](https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/packagetraits).

## Find and review your build artifacts

After compiling, locate your build artifacts.
Swift Package Manager places them in platform-specific directories. The location varies by build platform and architecture:

```bash
# Show where debug build artifacts are located
swift build --show-bin-path

# Show where release build artifacts are located
swift build --show-bin-path -c release
```

Typical paths include:

- **Linux (x86_64):** `.build/x86_64-unknown-linux/debug` or `.build/x86_64-unknown-linux/release`
- **macOS (Intel):** `.build/x86_64-apple-macosx/debug` or `.build/x86_64-apple-macosx/release`
- **macOS (Apple silicon):** `.build/arm64-apple-macosx/debug` or `.build/arm64-apple-macosx/release`

The `--show-bin-path` flag is particularly useful for deployment scripts, where you need to copy the build artifact to a specific location without hardcoding platform-specific paths.

### Build services for other platforms

Once you know where your build artifacts are located, you may need to target different platforms.
Swift build artifacts are both platform and architecture-specific.
Artifacts you create on macOS run only on macOS; those you create on Linux run only on Linux.
This creates a challenge for a common development pattern where developers work on macOS and deploy to Linux servers.

Many developers use Xcode for development but need to produce Linux artifacts for deployment.
Swift provides two main approaches for cross-platform building.

#### Build with Linux containers

On macOS, you can use [Container](https://github.com/apple/container) or the Docker CLI to create Linux build artifacts.
Apple publishes official Swift Docker images to [Docker Hub](https://hub.docker.com/_/swift), which provide complete Linux build environments.

To build your application using the latest Swift release image:

```bash
# Build with Container
container run -c 2 -m 8g --rm -it \
  -v "$PWD:/code" -w /code \
  swift:latest swift build

# Build with Docker
docker run --rm -it \
  -v "$PWD:/code" -w /code \
  swift:latest swift build
```

This command mounts your current directory into the container and runs `swift build` inside a Linux environment.
The `swift:latest` container image provides this environment.
This produces Linux-compatible build artifacts.

If you're on Apple silicon but need to target x86_64 Linux servers, specify the platform explicitly:

```bash
# Build with Container
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

The `--platform` flag runs the container with emulation using QEMU.
The `-e QEMU_CPU=max` environment variable turns on advanced CPU features in QEMU, including x86_64 support.

Emulation does not guarantee all processor-specific extensions are available, but this setting enables the broadest feature set supported by your system.

To build your code into a container, you typically use a container declaration, called a Dockerfile or Containerfile, which specifies all the steps used to assemble the container image that holds your build artifacts.
Container-based builds work particularly well in CI/CD pipelines and local development where you want to validate that your code builds cleanly on Linux. However, Docker containers can be slower than native builds, especially on Apple silicon where x86_64 containers run through emulation.

For a more detailed example of creating a container declaration to build and package your application, see [Packaging Swift Server Applications](./packaging.md).

#### Choose static or dynamic linking

By default, Swift build artifacts link the standard library dynamically. This keeps individual build artifacts smaller, and multiple programs can share a single copy of the Swift runtime.
However, it also means you need to ensure the Swift runtime is installed on your deployment target.

For deployment scenarios where you want more self-contained build artifacts, you can statically link the Swift standard library:

```bash
swift build -c release --static-swift-stdlib
```

The resulting build artifacts still depend on dynamically linking to glibc, but have fewer dependency requirements on the target system.

The resulting executables bundle the Swift runtime directly.
This creates self-contained artifacts with fewer system dependencies:

| Aspect | Dynamic Linking | Static Linking |
|--------|----------------|----------------|
| **Build artifact size** | Smaller (runtime not included) | Larger (adds to binary size for stdlib) |
| **Deployment complexity** | Requires Swift runtime on target system | Self-contained, no runtime needed |
| **Version management** | Must match runtime version on system | Each artifact includes its own runtime version |
| **Best for** | Containerized deployments with Swift runtime | VMs or bare metal with unknown configurations |

For containerized deployments, dynamic linking is usually preferable because the container already includes the Swift runtime. For deploying to VMs or bare metal where you don't control the system configuration, static linking can simplify operations significantly.

#### Cross-compile with the Static Linux SDK

If the performance overhead of Docker-based builds is a concern for your workflow, Swift 5.9 and later provide Static Linux SDKs that enable cross-compilation directly from macOS to Linux without using a container:

```bash
# Build for x86_64 Linux
swift build -c release --swift-sdk x86_64-swift-linux-musl

# Build for ARM64 Linux
swift build -c release --swift-sdk aarch64-swift-linux-musl
```

These SDK targets use musl libc (a lightweight C library) instead of glibc (the GNU C library) to produce statically linked build artifacts.
The resulting executables have minimal dependencies on the target Linux system, making them highly portable across Linux distributions.
However, they may be larger than dynamically linked equivalents.

Cross-compilation runs significantly faster than Docker-based builds.
It runs natively on your Mac's architecture without emulation.
The trade-off is build environment fidelity.
You verify that your code cross-compiles to Linux, not that it builds on an actual Linux system.
Another trade-off is that you can use only the static libraries that are available in the SDK, and your code cannot use `dlopen` or other tools to dynamically load libraries that may be available on the target system.

For most projects this distinction doesn't matter.
However, packages with complex C dependencies may behave differently when built natively on Linux versus cross-compiled.


### Inspect a binary

If you're uncertain what platform a binary was built for, use the `file` command to inspect the binary and determine its platform target.
The following example inspects a Swift executable (`MyServer`).

```
file .build/debug/MyServer
```

The output when compiled, in debug configuration, on macOS with Apple silicon:

```
.build/debug/MyServer: Mach-O 64-bit executable arm64
```

The output when compiled, in debug configuration, on Linux using a container on Apple silicon:

```
.build/debug/MyServer: ELF 64-bit LSB pie executable, 
  ARM aarch64, version 1 (SYSV), dynamically linked, 
  interpreter /lib/ld-linux-aarch64.so.1, for GNU/Linux 3.7.0, 
  BuildID[sha1]=ec68ac934b11eb7364fce53c95c42f5b83c3cb8d, 
  with debug_info, not stripped
```

The following is the output for the same executable compiled in debug configuration on macOS using the Container tool with x86_64 emulation (from the example above):

```
.build/debug/MyServer: ELF 64-bit LSB pie executable, 
  x86-64, version 1 (SYSV), dynamically linked, 
  interpreter /lib64/ld-linux-x86-64.so.2, for GNU/Linux 3.2.0, 
  BuildID[sha1]=40357329617ac9629e934b94415ff4078681b45a, 
  with debug_info, not stripped
```

Finally, that same binary compiled with the static Linux SDK (`swift build --swift-sdk x86_64-swift-linux-musl`):

```
.build/debug/MyServer: ELF 64-bit LSB executable, 
  x86-64, version 1 (SYSV), statically linked, 
  BuildID[sha1]=04ae4f872265b1e0d85ff821fd26fc102993b9f2, 
  with debug_info, not stripped
```
