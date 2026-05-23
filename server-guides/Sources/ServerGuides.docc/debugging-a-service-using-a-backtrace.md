# Debugging a service using a backtrace

Interpret a Swift service's crash trace to find the failing code on Linux.

## Overview

When your Swift service crashes on Linux, the runtime spawns a helper process,
`swift-backtrace`, that prints a stack trace that describes what was running and where it failed.
This article covers how to persist a trace from a container, read its structure,
map frames back to your source, recognize common crash patterns, and reproduce the crash locally.

For the configuration options the trace responds to, see <doc:swift-backtrace-configuration>.
To install the backtracer in a container image,
see <doc:packaging#Include-the-backtracer-in-the-runtime-image>.

### Persist backtraces from a container

When a container exits, its filesystem is gone,
so a crash that writes to a file inside the container loses the trace.
For durable, addressable crash files, write the trace to a volume that you mount when you run the container. For example, if you mounted a volume at `/var/crash-logs`, use the configuration:

```Dockerfile
ENV SWIFT_BACKTRACE=enable=yes,interactive=no,symbolicate=off,output-to=/var/crash-logs/
```

With `docker run` or Apple container, use the `-v` option to mount a volume;
with Kubernetes, mount a `PersistentVolume` or an `emptyDir`-backed volume at that path.

When `output-to` resolves to a directory, the backtracer writes each crash
to a uniquely named file inside it, so a burst of crashes won't overwrite
earlier traces.
The `symbolicate=off` setting keeps crash handling fast in production;
see <doc:debugging-a-service-using-a-backtrace#Map-a-frame-to-source> for how to resolve symbols afterward.

For the format of the captured file (text or JSON) and other output options,
see <doc:swift-backtrace-configuration#Output>.

### Read the trace structure

A captured trace has four parts: a header naming the signal,
a thread block for each thread the backtracer captures,
a frame list inside each thread, and an optional images list.
Here's a representative trace, abbreviated:

```
*** Program crashed: Bad pointer dereference at 0x0000000000000010 ***

Platform: Linux x86_64

Thread 0 "myservice" crashed:

0 0x000055a9c4001234 MyService.handleRequest(_:) + 132 in myservice at Handler.swift:84
1 0x000055a9c4002345 closure #1 in MyService.run() + 66 in myservice at Service.swift:42
2 0x000055a9c4003456 MyService.run() + 280 in myservice at Service.swift:28
3 0x00007f1234567890 swift_runJob + 48 in libswift_Concurrency.so
```

The header line names the **signal** and the **fault address**.
On Linux the common signals are `SIGSEGV` (bad memory access),
`SIGILL` and `SIGTRAP` (Swift runtime traps — see the table below),
`SIGABRT` (a deliberate abort, raised by `abort()` and by force-cast (`as!`) failures),
`SIGBUS` (misaligned access or access past the end of a memory-mapped file),
and `SIGFPE` (arithmetic exception, including integer division by zero).

Swift runtime traps (force-unwrap of `nil`, index-out-of-range, `try!` on a throwing call,
arithmetic overflow, `precondition`, `fatalError`) lower to a CPU trap instruction.
The kernel reports this as `SIGILL` on x86_64 (where the trap lowers to `ud2`)
and as `SIGTRAP` on aarch64 (where it lowers to `brk #1`).

The **thread line** identifies which thread crashed.
The backtracer captures only the crashed thread by default.
Use `SWIFT_BACKTRACE=threads=all` to see every thread which helps when diagnosing deadlocks.
For more information, see <doc:swift-backtrace-configuration#Captured-content>.

Each **frame line** has the form:

```
<index> <address> <symbol> + <offset> in <image> at <path>:<line>
```

The backtracer demangles the symbol by default.
The `at <path>:<line>` portion appears only when the binary carries DWARF debug information
and you run with `symbolicate=full` (the default).
Stripped production binaries with `symbolicate=off` produce frames with the address and image name only.

By default the trace includes registers and a list of loaded images for the crashed thread.
You can tune or suppress both; see <doc:swift-backtrace-configuration#Captured-content>.

### Map a frame to source

Symbolication — turning addresses into function names, files, and lines — has a real cost.
The default, `symbolicate=full`, walks DWARF for every captured frame,
which extends crash-handling time noticeably on a large binary.
Production services typically capture lightweight traces at crash time
and resolve symbols offline.

**At crash time, in production.**

Build production binaries with debug info stripped and run with `SWIFT_BACKTRACE=symbolicate=off`.
Each captured frame has its address and the image (binary or shared library) it belongs to —
enough to resolve to source later.
See <doc:swift-backtrace-configuration#Unwinding-and-symbolication> for the full set of `symbolicate` values
and the trade-offs between `off`, `fast`, and `full`.

**Post-mortem, on a developer or build machine.**

Keep the unstripped binary, or a separate DWARF debug-info package, that came from the same build as the trace.
With that artifact in hand:

```bash
# Resolve an address to a file:line and (mangled) symbol name.
llvm-symbolizer --obj=path/to/unstripped/myservice 0x000055a9c4001234

# Demangle the resulting Swift symbol names if needed.
swift demangle "$1MyService13handleRequestyAA8ResponseVAA7RequestVF"
```

**With a separate debug-info file.**

Ship debug information as a sidecar file rather than embedding it in the binary
(see <doc:building#Split-debug-information-into-a-sidecar-file>).
Place the sidecar where symbolicators look for it: first at the path that
`objcopy --add-gnu-debuglink` records (resolved relative to the binary,
then under `/usr/lib/debug/<binary-dir>/`),
then at the build-ID index at `/usr/lib/debug/.build-id/<first-2-hex>/<remaining-hex>.debug`,
where the hex digits come from the binary's build ID.
The `-debuginfo` and `-dbgsym` packages populate the build-ID index when you install them,
so a developer machine with the matching debug-info package installed resolves symbols transparently:

```
/usr/bin/MyServer                                # stripped, build ID ab12cd34...
/usr/lib/debug/.build-id/ab/12cd34....debug      # sidecar
```

With the sidecar in place, the same `llvm-symbolizer` invocation resolves to file and line:

```bash
llvm-symbolizer --obj=/usr/bin/MyServer 0x000055a9c4001234
```

`llvm-symbolizer` reads the build ID from the stripped binary, finds the matching sidecar on the index path, and pulls source locations from its DWARF.
If the build IDs don't match, the symbolicator falls back to address-only output;
confirm the pairing with `readelf -n` against both files (see <doc:building#Verify-build-IDs-match>).

Time isn't critical at this stage, so spending CPU on full demangling and inline-frame resolution is fine.
Tools like `addr2line` from Linux binutils work as well; pick whichever your build environment already has.

### Recognize the crash pattern

The signal and the message that the runtime prints just before the trace are usually enough
to identify the kind of bug.

| Signal and message | Typical cause |
|---|---|
| **trap** + `Fatal error: Index out of range` | Out-of-bounds collection access |
| **trap** + `Fatal error: Unexpectedly found nil while unwrapping an Optional value` | Force-unwrap (`!`) of a `nil` optional |
| **trap** + `Fatal error: 'try!' expression unexpectedly raised an error` | `try!` on a call that threw |
| **trap** + `Fatal error: Arithmetic operation ... overflow` | Trapping integer arithmetic |
| **trap** + `Precondition failed: <message>` or `Fatal error: <message>` | `precondition()` or `fatalError()` call |
| `SIGABRT` + `Could not cast value of type ...` | Force-cast (`as!`) failure |
| `SIGABRT` | Direct `abort()` call from Swift or a C dependency |
| `SIGSEGV` with frames in your unsafe-pointer or C-interop code | Memory-safety violation in that code |
| `SIGSEGV` with frames only in libc or runtime | Likely heap corruption from earlier code |
| `SIGSEGV` with a deeply uniform recursive frame pattern | Stack overflow |

For runtime-trap crashes (the **trap** rows in the previous table), the relevant fix is almost always
in the topmost frame of *your* code in the trace —
the standard library trap is a few frames up from there.
For `SIGSEGV` in unsafe code or dependencies, the topmost frame names the call that touched bad memory,
but the actual cause is often earlier code that produces a bad pointer or frees memory still in use.

### Reproduce locally

Once you've identified the suspect call site, capture the input that triggered it —
request body, environment variables, mounted file, and configured client state —
and rerun the service locally.
Two paths are useful:

- **Run under `lldb`.**
  Launch the service with `lldb` and let it stop at the crash for live inspection of variables and threads.
- **Rerun with the interactive backtracer.**
  Set `SWIFT_BACKTRACE=interactive=yes` so the backtracer drops into a debugger-like prompt at crash time
  instead of printing and exiting.
  See <doc:swift-backtrace-configuration#Enabling-and-presentation> for the option.
  Interactive mode requires a TTY, so it's a developer-machine tool, not a production setting.

### Diagnose missing backtraces

If a crash produced no trace at all, or one that ends after a few frames,
the cause is almost always one of the following:

- term The helper binary isn't in the runtime image:
  Slim and distroless base images don't ship `swift-backtrace`.
  Copy `swift-backtrace-static` into the image — see <doc:packaging#Include-the-backtracer-in-the-runtime-image>.

- term The runtime can't find the helper:
  The runtime searches a fixed set of locations relative to a Swift root
  directory; it doesn't consult `PATH`.
  In a statically linked binary that doesn't follow the toolchain layout,
  set `SWIFT_BACKTRACE=swift-backtrace=<absolute-path>` or `SWIFT_ROOT=<root>`.
  See <doc:swift-backtrace-configuration#Advanced-options> for the search order.

- term `/proc` isn't mounted:
  The backtracer enumerates threads and locates loaded images through `/proc/<pid>/`.
  Containers running with a stripped-down filesystem don't always have it mounted; re-enable `/proc`.

- term Frame pointers are missing:
  The fast unwinder follows frame pointers, so a binary built with `-fomit-frame-pointer`
  (or a C/C++ dependency built that way) produces traces that stop at the first frame without one.
  Current Swift toolchains preserve frame pointers in release builds on Linux server architectures.
  Force the precise unwinder with `SWIFT_BACKTRACE=unwind=precise` to use DWARF instead,
  or rebuild affected dependencies with `-fno-omit-frame-pointer`.
