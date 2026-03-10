# Packaging Swift services with containers

Build a container that includes your Swift service with its resources and dependencies.

## Overview

Running your service in the cloud requires configuration, dependencies, and resources.
Containers provide a standard way to package your service along with everything it needs.
You build a container image using tools such as [Docker](https://www.docker.com) or [Container](https://github.com/apple/container),
then deploy that image to a virtual machine or cloud hosting infrastructure
such as a Kubernetes cluster.

A container image packages everything your service needs to run into a single, portable artifact.
The image's filesystem contains:

- The compiled executable for your service
- Linux system libraries and runtime dependencies
- Resources your service requires, such as configuration files or static assets

The image also declares runtime metadata that controls how a container starts:

- A default command to run
- Ports to expose for incoming network connections
- Default values for environment variables that configure your service

A container image is built from layers of filesystem changes, stacked on top of each other.
Each layer is read-only and shared across images that use it, which makes images efficient to store and transfer.
When you run an image, the container runtime adds a writable layer on top and starts your service as a container:
a running instance of the image in its own isolated environment.

At runtime, your deployment environment can mount additional filesystems
into a container, such as configuration maps or secrets, to provide values that vary between environments.

### Swift container images

The Swift project publishes container images on [Docker Hub](https://hub.docker.com/_/swift) that you can use to build and run your services.
These images come in two forms:

- **Full SDK images** include the Swift compiler, standard library, and development tools.
  For example, `swift:6.2-noble` provides Swift 6.2 on Ubuntu 24.04.

- **Slim runtime images** include only the Swift runtime libraries on a minimal Linux distribution.
  Use these as the final stage in a multi-stage build to keep your deployed image small.
  For example, `swift:6.2-noble-slim` provides a minimal Ubuntu 24.04 image with the Swift runtime.

You can also use a plain Linux distribution image, such as `ubuntu:noble`, as your base image when you statically link the Swift standard library.

> Tip: Pin your images to a specific Swift version, such as `swift:6.2-noble`, rather than using the `latest` tag.
> Pinning the image makes your builds more reproducible and prevents unexpected changes when a new Swift release is published.

Your organization may provide vetted base images that reflect its security policies and operational best practices.
If it does, build on those base images.

### Build a container image

A single-stage build compiles and packages your service in one image.
Create a file named `Dockerfile` at the root of your project:

```Dockerfile
FROM swift:6.2-noble

WORKDIR /workspace
COPY . /workspace

RUN swift build -c release --static-swift-stdlib

EXPOSE 8080
CMD [".build/release/<executable-name>"]
```

The `EXPOSE` directive declares which port your service listens on.
It doesn't publish the port — you do that at runtime with `-p` — but it documents the intended network interface, and some orchestrators and tools use it.

Build the image.
The `-t` flag tags the image with a name and version:

```bash
container build -t <my-app>:latest .
```

Tags identify images locally and determine where images go when you push them to a registry.
The convention is `<name>:<version>` — for example, `my-app:1.0` or `my-app:latest`.
Choose a name that matches your service and a version that reflects the build.
If you don't include `:` and a version string, the tools default to `:latest`.

> Note: The examples in this guide use the `container` command from [Apple's container tool](https://github.com/apple/container).
> The `docker` command accepts the same arguments and works the same way.

This approach works for a quick local build,
but the image includes the full Swift compiler and development tools,
which add hundreds of megabytes that your service never uses at runtime
and expand the attack surface of the deployed container.

### Slim the image with a multi-stage build

A [multi-stage build](https://docs.docker.com/build/building/multi-stage/) separates compilation from packaging.
The first stage compiles your service using the full SDK image.
The second stage copies only the compiled executable into a minimal runtime image,
producing a final image that is a fraction of the size.

```Dockerfile
# Stage 1: Build
FROM swift:6.2-noble AS builder

WORKDIR /workspace
COPY . /workspace

RUN swift build -c release --static-swift-stdlib

# Stage 2: Package
FROM ubuntu:noble
COPY --from=builder /workspace/.build/release/<executable-name> /

EXPOSE 8080
CMD ["/<executable-name>"]
```

The `--static-swift-stdlib` flag links the Swift standard library into your executable,
so the final image doesn't need the Swift runtime installed.
If your service uses `FoundationNetworking` or `FoundationXML`, use the image `swift:6.2-noble-slim` for your runtime-based image, instead of `ubuntu:noble`.
This image includes system libraries that those frameworks use (`libcurl` and `libxml2`).

Build and run the image the same way:

```bash
container build -t <my-app>:latest .
```

> Tip: Create a `.dockerignore` file at the root of your project to exclude directories
> like `.build/` and `.git/` from the build context.
> Without it, `COPY . /workspace` sends everything to the build daemon,
> which slows builds and can bloat image layers.

### Cache build artifacts to speed up rebuilds

The Dockerfiles above copy all source files into the image and build from scratch every time.
For a small project, this is fine, but as your service grows,
rebuilding all dependencies on every change slows down the development cycle.

[Docker BuildKit cache mounts](https://docs.docker.com/build/cache/)
persist directories across builds.
Swift Package Manager's resolved packages and compiled artifacts survive between invocations.
Bind mounts pass in only the files each step needs
without copying them into the image layer,
which keeps the cache effective even when source files change.

The following Dockerfile resolves dependencies and builds in separate steps,
each with a cache mount for the build directory:

```Dockerfile
# Stage 1: Build
FROM swift:6.2-noble AS builder

WORKDIR /workspace

# Resolve dependencies — re-runs only when Package.swift or Package.resolved change
RUN --mount=type=cache,target=/tmp/.build,sharing=locked \
    --mount=type=bind,source=Package.swift,target=Package.swift \
    --mount=type=bind,source=Package.resolved,target=Package.resolved \
    swift package resolve --force-resolved-versions --build-path /tmp/.build

# Build the executable — reuses cached dependencies and prior compilation results
RUN --mount=type=cache,target=/tmp/.build,sharing=locked \
    --mount=type=bind,source=Package.swift,target=Package.swift \
    --mount=type=bind,source=Package.resolved,target=Package.resolved \
    --mount=type=bind,source=./Sources,target=./Sources \
    swift build -c release --static-swift-stdlib \
      --product <executable-name> --build-path /tmp/.build && \
    cp /tmp/.build/release/<executable-name> /usr/local/bin/

# Stage 2: Package
FROM ubuntu:noble
COPY --from=builder /usr/local/bin/<executable-name> /

EXPOSE 8080
CMD ["/<executable-name>"]
```

Cache mounts (`--mount=type=cache`) keep the `/tmp/.build` directory across builds.
Swift Package Manager reuses previously resolved packages and compiled modules.
Bind mounts (`--mount=type=bind`) pass specific files into each `RUN` step
without adding them to the image layer.
Changes to your source files don't invalidate the cache.

Because cache mounts aren't part of the final image layer,
the build step explicitly copies the compiled binary to a known path
before the second stage picks it up.

> Note: If your project includes a `Snippets/` directory or other non-source directories
> referenced by `Package.swift`, add additional bind mounts for those directories
> in the build step.

### Build for a different platform

If your development machine and deployment target use different CPU architectures —
for example, building on Apple Silicon (arm64) and deploying to x86_64 cloud infrastructure —
you need to specify the target platform when building the image.

With `docker`, use the `--platform` flag:

```bash
docker build --platform linux/amd64 -t <my-app>:latest .
```

To build for multiple architectures in a single command,
use `docker buildx`, which produces a multi-architecture image manifest:

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t <my-app>:latest .
```

See [Docker's multi-platform build documentation](https://docs.docker.com/build/building/multi-platform/)
for setup details, including creating a builder that supports cross-platform emulation.

Apple's `container` tool builds for the host architecture by default.
To target a different architecture, use the `--arch` flag:

```bash
container build --arch amd64 -t <my-app>:latest .
```

> Note: Cross-platform builds use emulation, which is significantly slower than native builds.
> When possible, build on infrastructure that matches your deployment architecture,
> such as an x86_64 CI runner for x86_64 deployments.

### Push to a registry

A container registry stores and distributes images so that other systems can pull and run them.
To push an image, tag it with the registry's hostname and repository path,
then push the tagged image.

Using [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) as an example:

```bash
container tag <my-app>:latest ghcr.io/<github-username>/<my-app>:latest
container push ghcr.io/<github-username>/<my-app>:latest
```

Pushing requires authentication with the registry.
See [Docker's registry authentication documentation](https://docs.docker.com/reference/cli/docker/login/)
for how to log in from the command line.

> Tip: If you're using `container` instead of `docker`, authenticate with `container registry login`.

You can also apply the full registry tag directly during the build:

```bash
container build -t ghcr.io/<github-username>/<my-app>:1.0 .
container push ghcr.io/<github-username>/<my-app>:1.0
```

> Note: Your organization or cloud hosting provider may operate its own container registry.
> Check with your team for the correct registry hostname and authentication method.

### Run locally to verify and debug

Run a container from your image to verify it starts correctly:

```bash
container run <my-app>:latest
```

To expose a port from the container to your host machine, use the `-p` flag.
The first number is the host port, the second is the container port:

```bash
container run -p 8080:8080 <my-app>:latest
```

Pass environment variables with `-e` to configure your service without rebuilding:

```bash
container run -p 8080:8080 -e LOG_LEVEL=debug <my-app>:latest
```

Mount a local directory into the container with `-v` to provide configuration files
or inspect output your service writes to disk:

```bash
container run -v "$PWD/config:/config" <my-app>:latest
```

For interactive debugging, combine `--rm` and `-it`.
The `--rm` flag removes the container when it exits, so stopped containers don't accumulate.
The `-it` flags attach an interactive terminal.
You can interrupt the process with Control-C, or override the default command to explore the container's filesystem:

```bash
container run --rm -it <my-app>:latest /bin/sh
```

This drops you into a shell inside the container.
You can inspect the filesystem, check that files are in the expected locations, and verify the runtime environment.
