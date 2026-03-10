# Packaging Swift services with containers

Build a container that includes your Swift service with its resources and dependencies.

## Overview

<!-- writing for someone who may be new to containers, not know much about using them -->
<!-- assume familiar to Swift development and server use case specific libraries -->

Running your service in the cloud requires configuration, dependencies, and resources.
Containers provide a standard way to package your service along with everything it needs.
You build a container image using tools such as [Docker](https://www.docker.com) or [Container](https://github.com/apple/container),
then deploy that image to a virtual machine or cloud hosting infrastructure
such as a Kubernetes cluster.

<!-- describe what a container is, and does -->
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

<!-- wrap up overview by outlining the details below for creating your own images -->

### Swift container images

The Swift project publishes container images on [Docker Hub](https://hub.docker.com/_/swift) that you can use to build and run your services.
These images come in two forms:

- **Full SDK images** include the Swift compiler, standard library, and development tools.
  For example, `swift:6.2-noble` provides Swift 6.2 on Ubuntu 24.04.

- **Slim runtime images** include only the Swift runtime libraries on a minimal Linux distribution.
  Use these as the final stage in a multi-step image build to keep your deployed image small.
  For example, `swift:6.2-noble-slim` provides a minimal Ubuntu 24.04 image with the Swift runtime.

You can also use a plain Linux distribution image, such as `ubuntu:noble` or `ubuntu:noble-slim`, as your base image when you statically link the Swift standard library.

<!-- check to see if there's a better location to reference for what the Swift project provides, linking to that instead of this sentence. -->
The Swift Docker images are available for Ubuntu, Debian, Amazon Linux, Fedora, and Red Hat UBI.

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

CMD [".build/release/<executable-name>"]
```

Build the image, using the `-t` flag tags the image with a name and version:

```bash
container build -t <my-app>:latest .
```

Tags identify images locally and determine where images go when you push them to a registry.
The convention is `<name>:<version>` — for example, `my-app:1.0` or `my-app:latest`.
Choose a name that matches your service and a version that reflects the build.
If you don't include `:` and a version string, the tools default to `:latest`.

> Note: The examples in this guide use the `container` command from [Apple's container tool](https://github.com/apple/container).
> The `docker` command accepts the same arguments and works the same way.

This approach works, and is handy for a quick local build for testing locally.
However, the image includes the full Swift compiler and development tools that
adds hundreds of megabytes your service never uses at runtime
and increases the attack surface of the deployed container.

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
FROM ubuntu:noble-slim
COPY --from=builder /workspace/.build/release/<executable-name> /

CMD ["/<executable-name>"]
```

The `--static-swift-stdlib` flag links the Swift standard library into your executable,
so the final image does not need the Swift runtime installed.
If your service uses `FoundationNetworking` or `FoundationXML`, use the image `swift:6.2-noble-slim` for your runtime based image, instead of `ubuntu:noble-slim`. 
This image includes system libraries that those frameworks use (`libcurl` and `libxml2`).

Build and run the image the same way:

```bash
container build -t <my-app>:latest .
```

### Cache build artifacts to speed up rebuilds

The Dockerfiles above copy all source files into the image, and build from scratch every time.
For a small project this is fine, but as your service grows,
rebuilding all dependencies on every change slows down the development cycle.

[Docker BuildKit cache mounts](https://docs.docker.com/build/cache/)
persist directories across builds so that Swift Package Manager's resolved packages
and compiled artifacts survive between invocations.
Combined with bind mounts that pass in only the files each step needs,
this avoids copying files into the image layer and keeps the cache effective
even when source files change.

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
FROM ubuntu:noble-slim
COPY --from=builder /usr/local/bin/<executable-name> /

CMD ["/<executable-name>"]
```

Cache mounts (`--mount=type=cache`) keep the `/tmp/.build` directory across builds
so Swift Package Manager reuses previously resolved packages and compiled modules.
Bind mounts (`--mount=type=bind`) pass specific files into each `RUN` step
without adding them to the image layer,
which means changes to your source files don't invalidate the cache.

Because cache mounts aren't part of the final image layer,
the build step explicitly copies the compiled binary to a known path
before the second stage picks it up.

> Note: If your project includes a `Snippets/` directory or other non-source directories
> referenced by `Package.swift`, add additional bind mounts for those directories
> in the build step.

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

> Tip: If you're using `container` instead of `docker`, then use the command `container registry login` to authenticate a registry.

You can also apply the full registry tag directly during the build:

```bash
container build -t ghcr.io/<github-username>/<my-app>:1.0 .
container push ghcr.io/<github-username>/<my-app>:1.0
```

> Note: Your organization or cloud hosting provider may operate its own container registry.
> Check with your team for the correct registry hostname and authentication method.

### run locally to vet or debug


## Docker

Using Docker's tooling, we can build and package the application as a Docker image, publish it to a Docker repository, and later launch it directly on a server or on a platform that supports Docker deployments such as [Kubernetes](https://kubernetes.io). Many public cloud providers including AWS, GCP, Azure, IBM and others encourage this kind of deployment.

Here is an example `Dockerfile` that builds and packages the application using Ubuntu:

```Dockerfile
#------- build -------
FROM swift:6.2-noble AS builder

WORKDIR /workspace

# copy the source to the docker image
COPY . /workspace

RUN swift build -c release --static-swift-stdlib

#------- package -------
FROM ubuntu:noble
# copy executables
COPY --from=builder /workspace/.build/release/<executable-name> /

# set the entry point (application name)
CMD ["<executable-name>"]
```

To create a local Docker image from the `Dockerfile` use the `docker build` command from the application's source location, e.g.:

```bash
$ docker build . -t <my-app>:<my-app-version>
```

To test the local image use the `docker run` command, e.g.:

```bash
$ docker run <my-app>:<my-app-version>
```

Finally, use the `docker push` command to publish the application's Docker image to a Docker repository of your choice, e.g.:

```bash
$ docker tag <my-app>:<my-app-version> <docker-hub-user>/<my-app>:<my-app-version>
$ docker push <docker-hub-user>/<my-app>:<my-app-version>
```

At this point, the application's Docker image is ready to be deployed to the server hosts (which need to run docker), or to one of the platforms that supports Docker deployments.

See [Docker's documentation](https://docs.docker.com/engine/reference/commandline/) for more complete information about Docker.

### Distroless

[Distroless](https://github.com/GoogleContainerTools/distroless) is a project by Google that attempts to create minimal images containing only the application and its runtime dependencies. They do not contain package managers, shells or any other programs you would expect to find in a standard Linux distribution.

Since distroless supports Docker and is based on Debian, packaging a Swift application on it is fairly similar to the Docker process above. Here is an example `Dockerfile` that builds and packages the application on top of distroless's C++ base image:

```Dockerfile
#------- build -------
FROM swift:6.2-noble AS builder

WORKDIR /workspace

# copy the source to the docker image
COPY . /workspace

RUN swift build -c release --static-swift-stdlib

#------- package -------
# Running on distroless C++ since it includes
# all(*) the runtime dependencies Swift programs need
FROM gcr.io/distroless/cc-debian12
# copy executables
COPY --from=builder /workspace/.build/release/<executable-name> /

# set the entry point (application name)
CMD ["<executable-name>"]
```

Note the above uses `gcr.io/distroless/cc-debian12` as the runtime image which should work for Swift programs that do not use `FoundationNetworking` or `FoundationXML`. In order to provide more complete support we (the community) could put in a PR into distroless to introduce a base image for Swift that includes `libcurl` and `libxml` which are required for `FoundationNetworking` and `FoundationXML` respectively.

## Archive (Tarball, ZIP file, etc.)

Since cross-compiling Swift for Linux is not (yet) supported on Mac or Windows, we need to use virtualization technologies like Docker to compile applications we are targeting to run on Linux.

That said, this does not mean we must also package the applications as Docker images in order to deploy them. While using Docker images for deployment is convenient and popular, an application can also be packaged using a simple and lightweight archive format like tarball or ZIP file, then uploaded to the server where it can be extracted and run.

Here is an example of using Docker and `tar` to build and package the application for deployment on Ubuntu servers:

First, use the `docker run` command from the application's source location to build it:

```bash
$ docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  swift:6.2-noble \
  /bin/bash -cl "swift build -c release --static-swift-stdlib"
```

Note we are bind mounting the source directory so that the build writes the build artifacts to the local drive from which we will package them later.

Next we can create a staging area with the application's executable:

```bash
$ docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  swift:6.2-noble  \
  /bin/bash -cl ' \
     rm -rf .build/install && mkdir -p .build/install && \
     cp -P .build/release/<executable-name> .build/install/'
```

Note this command could be combined with the build command above--we separated them to make the example more readable.

Finally, create a tarball from the staging directory:

```bash
$ tar cvzf <my-app>-<my-app-version>.tar.gz -C .build/install .
```

We can test the integrity of the tarball by extracting it to a directory and running the application in a Docker runtime container:

```bash
$ cd <extracted directory>
$ docker run -v "$PWD:/app" -w /app ubuntu:noble ./<executable-name>
```

Deploying the application's tarball to the target server can be done using utilities like `scp`, or in a more sophisticated setup using configuration management system like `chef`, `puppet`, `ansible`, etc.


## Source Distribution

Another distribution technique popular with dynamic languages like Ruby or Javascript is distributing the source to the server, then compiling it on the server itself.

To build Swift applications directly on the server, the server must have the correct Swift toolchain installed. [Swift.org](/download/#linux) publishes toolchains for a variety of Linux distributions, make sure to use the one matching your server Linux version and desired Swift version.

The main advantage of this approach is that it is easy. Additional advantage is the server has the full toolchain (e.g. debugger) that can help troubleshoot issues "live" on the server.

The main disadvantage of this approach that the server has the full toolchain (e.g. compiler) which means a sophisticated attacker can potentially find ways to execute code. They can also potentially gain access to the source code which might be sensitive. If the application code needs to be cloned from a private or protected repository, the server needs access to credentials which adds additional attack surface area.

In most cases, source distribution is not advised due to these security concerns.

## Static linking and Curl/XML

**Note:** if you are compiling with `-static-stdlib` and using Curl with FoundationNetworking or XML with FoundationXML you must have libcurl and/or libxml2 installed on the target system for it to work.
