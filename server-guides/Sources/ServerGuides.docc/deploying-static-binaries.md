# Deploying Swift services as static binaries

Build fully self-contained Swift executables and deploy them
to minimal containers or directly to Linux machines.

## Overview

The Static Linux SDK produces Swift executables with no external dependencies —
not even the C library.
This lets you run the binary on any Linux machine regardless of distribution,
which opens deployment options that aren't possible with dynamically linked builds.

You can copy the binary into a `scratch` or distroless container image
that contains nothing but your executable,
or skip containers entirely and copy the binary to a virtual machine.
Either way, the result is the smallest possible deployment artifact
with the smallest possible attack surface.

This article walks you through this process:
- install the SDK
- build a static binary
- deploy it

### Install the Static Linux SDK

The Static Linux SDK requires an open-source Swift toolchain from [swift.org](https://www.swift.org/install/).
The toolchain bundled with Xcode doesn't support SDK-based cross-compilation.

Install the SDK with `swift sdk install`, providing the URL for your Swift version.

Check the [Swift Install page](https://www.swift.org/install/) for the Swift SDK for Static Linux under SDK bundles
for the current install command.
The command follows this form:

```bash
swift sdk install <url> --checksum <checksum>
```

After installation, verify the SDK is available:

```bash
swift sdk list
```

The output includes SDK names like `x86_64-swift-linux-musl`
and `aarch64-swift-linux-musl` that you use in build commands.

### Build a static binary

Build your service with the `--swift-sdk` flag,
specifying the target architecture:

```bash
swift build -c release --swift-sdk aarch64-swift-linux-musl
```

For x86_64 targets, use the corresponding SDK name:

```bash
swift build -c release --swift-sdk x86_64-swift-linux-musl
```

This produces a statically linked ELF binary in `.build/release/`
that you can copy to any Linux system and run directly.
The SDK uses [Musl](https://musl.libc.org) instead of Glibc,
and statically links everything the executable needs —
including the Swift standard library, Foundation, and system libraries
like libcurl and libxml2.

> Note: Because Musl replaces Glibc,
> some packages that import C libraries need changes.
> For some, the fix is changing import lines from `Glibc` to `Musl`.
> The Swift standard library and Foundation handle this automatically.

### Deploy to a distroless container image

A distroless container image contains no operating system utilities —
no package manager, no shell, nothing but what you put in it.
This eliminates an entire class of potential vulnerabilities
because there are no extra programs an attacker could exploit.

Because the Static Linux SDK cross-compiles from your development machine,
you don't need a multi-stage Dockerfile with a Swift SDK builder image.
Build the binary locally, then use a minimal `Dockerfile`
that copies the pre-built executable into a `scratch` image —
a completely empty base image:

```bash
swift build -c release --swift-sdk aarch64-swift-linux-musl
```

```Dockerfile
FROM scratch
COPY .build/release/<executable-name> /

EXPOSE 8080
ENTRYPOINT ["/<executable-name>"]
```

The final image contains a single file: your executable.

If your service makes outbound TLS connections,
the image also needs CA certificates.
Copy them from any Linux image at build time:

```Dockerfile
FROM ubuntu:noble AS certs
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates

FROM scratch
COPY --from=certs /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY .build/release/<executable-name> /

EXPOSE 8080
ENTRYPOINT ["/<executable-name>"]
```

Build the container image:

```bash
container build -t <my-app>:latest .
```

> Note: A `scratch`-based image doesn't include a shell,
> so you can't use `container run --rm -it <my-app> /bin/sh` to debug.
> If you need a shell for troubleshooting,
> use a slim base image during development and switch to `scratch` for production.

### Build and publish with Swift Container Plugin

The [Swift Container Plugin](https://github.com/apple/swift-container-plugin)
combines the build, packaging, and registry push into a single command.
It builds your service with the Static Linux SDK,
packages the executable into a container image,
and publishes the image to a container registry — all without a Dockerfile.

Add the plugin to your `Package.swift`:

```swift
dependencies: [
    .package(url: "https://github.com/apple/swift-container-plugin", from: "0.1.0"),
],
```

Then build and publish:

```bash
swift package --swift-sdk aarch64-swift-linux-musl \
    build-container-image --repository registry.example.com/my-app
```

The plugin produces a minimal image equivalent to a `scratch`-based Dockerfile
and pushes it to the specified registry.
Run the published image with any container runtime:

```bash
container run -p 8080:8080 registry.example.com/my-app
```

> Tip: The container plugin is especially useful in CI pipelines
> where you want a single, repeatable command
> that produces a ready-to-deploy image.

### Deploy directly to a Linux machine

Containers aren't required.
A static binary runs on any Linux machine,
so you can copy it directly to a virtual machine or bare-metal server:

```bash
scp .build/release/<executable-name> user@server:/usr/local/bin/
```

On the server, run the executable directly:

```bash
ssh user@server /usr/local/bin/<executable-name>
```

No Swift runtime, no system libraries, and no container runtime needed.
Use your preferred configuration management tool —
Ansible, Chef, Puppet, or a simple shell script —
to automate deployment across multiple hosts.

> Tip: Pair direct deployment with a systemd service unit
> to manage your service's lifecycle, restart policy, and logging.
