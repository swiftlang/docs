# Swift backtrace configuration

Control how the Swift runtime captures and reports stack traces when your service crashes.

## Overview

When a Swift program receives a fatal signal on Linux —
`SIGSEGV`, `SIGABRT`, `SIGBUS`, `SIGFPE`, `SIGILL`, `SIGTRAP`, or `SIGQUIT` —
the runtime's signal handler suspends the program's threads and launches a separate
helper process, `swift-backtrace`, to gather and format a crash report.
The helper reads the crashed process's memory, walks each thread's stack,
symbolicates frames, and writes the result before the original process exits.

This two-process design keeps the signal handler small and signal-safe,
and lets the backtracer use ordinary Swift code to format output and look up symbols.

The runtime only installs its signal handler for a given signal if no handler
is already in place, and only sets up an alternate signal stack if one isn't
already configured.
A library that installs its own handler for one of the catchable signals —
even `SIG_IGN` — silently disables backtracing for that signal.

You configure the backtracer through the `SWIFT_BACKTRACE` environment variable.
It accepts a comma-separated list of `key=value` pairs:

```bash
SWIFT_BACKTRACE=enable=yes,interactive=no,format=json,output-to=/var/log/crashes/
```

The defaults target interactive use at a terminal.
For server deployments, the most common adjustments are
`interactive=no` for containers without a TTY,
and either `format=json` or `output-to=<path>` so traces land somewhere your log pipeline can ingest.

### Enabling and presentation

| Option | Values | Default | Notes |
|---|---|---|---|
| `enable` | `yes`, `no`, `tty` | `yes` | `tty` enables backtracing only when stderr is a terminal. |
| `interactive` | `yes`, `no`, `tty` | `tty` | Interactive mode drops into a debugger-like prompt; disable for non-TTY containers. |
| `color` | `yes`, `no`, `tty` | `tty` | ANSI color in output. |
| `demangle` | `yes`, `no` | `yes` | Demangle Swift symbols in frames. |
| `preset` | `friendly`, `medium`, `full`, `auto` | `auto` | Bundles `threads`, `registers`, and `images` defaults. `friendly` is concise; `full` shows everything. |

### Captured content

| Option | Values | Default | Notes |
|---|---|---|---|
| `threads` | `crashed`, `all`, `preset` | `preset` | `crashed` shows only the failing thread; `all` is useful for deadlocks. |
| `registers` | `none`, `crashed`, `all`, `preset` | `preset` | Whether to dump CPU registers alongside frames. |
| `images` | `none`, `mentioned`, `all`, `preset` | `preset` | Loaded shared-library list; `mentioned` includes only those referenced by frames. |
| `limit` | integer, `none` | `64` | Maximum frames per thread. Prevents runaway output on infinite recursion. |
| `top` | integer | `16` | When `limit` truncates, always keep this many frames from the top of stack. |
| `sanitize` | `yes`, `no`, `preset` | `preset` | Strip PII from paths in captured frames. Has no effect on Linux; the runtime parses the option but doesn't transform paths outside macOS. |

When the captured stack has more than `limit` frames, the backtracer keeps
`top` frames from the top of the stack (the deepest, most recent calls)
and `limit - 1 - top` frames from the bottom (the entry into the program),
joined by a `...` marker.
For example, a 10-frame stack with `limit=5,top=2` shows frames 10, 9, then
`...`, then 2, 1 — letting you see both where execution started and where
the fault occurred when a runaway recursion blows past the limit.

### Unwinding and symbolication

| Option | Values | Default | Notes |
|---|---|---|---|
| `unwind` | `auto`, `fast`, `precise` | `auto` | `fast` uses frame pointers only; `precise` consults DWARF unwind info. `auto` picks per-frame. |
| `symbolicate` | `full`, `fast`, `off` | `full` | `full` resolves inlined frames using DWARF; `fast` resolves only the outermost symbol; `off` reports raw addresses. |
| `cache` | `yes`, `no` | `yes` | Cache symbol lookups across frames. |

Symbolication quality depends on what's in your binary.
Stripped binaries report addresses without symbol names.
To embed DWARF directly in a release binary, or to split debug info into a sidecar file the symbolicator can find by build ID, see <doc:building#Preserve-debug-information-for-symbolication>.
To resolve addresses from a captured trace using either form, see <doc:debugging-a-service-using-a-backtrace#Map-a-frame-to-source>.

### Output

| Option | Values | Default | Notes |
|---|---|---|---|
| `format` | `text`, `json` | `text` | JSON is structured for log aggregators. |
| `output-to` | `stderr`, `stdout`, file path, directory path | `stderr` | If the path resolves to an existing directory, the backtracer writes each crash to a uniquely named file inside it. Otherwise the backtracer treats the path as a filename. |

A text-format trace looks like this (abbreviated; registers and image list omitted):

```
*** Signal 11: Backtracing from 0xaaaaaaaa1804... done ***

*** Program crashed: Bad pointer dereference at 0x0000000000000004 ***

Thread 0 crashed:

0                  0x0000aaaaaaaa1804 MyService.handleRequest(_:) + 180 in myservice at /work/Sources/myservice/Service.swift:7:21
1 [ra]             0x0000aaaaaaaa11a8 closure #1 in MyService.run() + 15 in myservice at /work/Sources/myservice/Service.swift:13:17
2 [ra]             0x0000aaaaaaaa117c MyService.run() + 27 in myservice at /work/Sources/myservice/Service.swift:15:9
3 [async]          0x0000aaaaaaaa1218 static Main.main() in myservice at /work/Sources/myservice/Service.swift:22
4 [async] [system] 0x0000aaaaaaaa1344 static Main.$main() in myservice at //<compiler-generated>
...
```

Bracketed labels denote frame attributes: `[ra]` for return-address frames,
`[async]` for async resumption points, `[system]` for compiler-generated or
runtime frames, and `[thunk]` for compiler-generated bridges. The same
attributes appear in JSON output as the frame's `kind` field and the
`system` and `thunk` Boolean markers.
For the same crash represented as JSON, see <doc:swift-backtrace-configuration#Example-payload>.

JSON output produces one object per crash, with a top-level
`description` describing the failure, a `faultAddress`, `platform`,
`architecture`, and a `threads` array containing per-thread frame lists,
along with registers when configured. See <doc:swift-backtrace-configuration#JSON-crash-log-schema>
for the full schema.

Pipe it to your log shipper or write to a mounted volume:

```bash
SWIFT_BACKTRACE=format=json,output-to=/var/crash-logs/
```

### Advanced options

| Option | Values | Default | Notes |
|---|---|---|---|
| `timeout` | duration (`30s`), `none` | `30s` | How long the runtime waits for the backtracer to finish. |
| `swift-backtrace` | path | auto-detected | Override the implicit search and use the given absolute path directly. Required for statically linked binaries in minimal containers, where the implicit search can't reliably find the helper. |
| `warnings` | `enabled`, `suppressed` | `enabled` | Diagnostic messages from the backtracer itself. |
| `close-fds` | `yes`, `no` | `no` | Close all open file descriptors in the crashing process before gathering the trace. Useful in CI environments where leaked file descriptors can cause resource contention. |

The runtime locates `swift-backtrace` by deriving a Swift root directory
and searching a fixed set of subdirectories underneath it.
The root is whichever of these the runtime finds first:

1. The value of the `SWIFT_ROOT` environment variable, if set.
2. A path computed from the location of `libswiftCore` (when the runtime is
   dynamically linked).
3. A path computed from the location of the running executable (when the
   runtime is statically linked into the program).

Within the root, the runtime checks these locations in order, where
`<platform>` is the Swift platform name (for example, `linux`) and `<arch>` is the
CPU architecture (for example, `x86_64` or `aarch64`):

```
<root>/libexec/swift/<platform>
<root>/libexec/swift/<platform>/<arch>
<root>/libexec/swift
<root>/libexec/swift/<arch>
<root>/bin
<root>/bin/<arch>
<root>
```

The runtime doesn't consult `PATH`. If none of the above contain the helper,
crashes go uncaught — set `SWIFT_BACKTRACE=swift-backtrace=<path>` to point
at it explicitly, or place the binary at one of the search locations.

### JSON crash log schema

When `format=json`, the backtracer emits one JSON object per crash.
Addresses appear as hexadecimal strings (with `0x` prefix); raw byte data
such as captured memory or build IDs appears as un-prefixed hexadecimal
strings with no inter-byte whitespace.
The backtracer omits Boolean fields when `false`, and omits unknown or empty
values entirely to save space.

#### Top-level fields

The following fields are always present:

| Field | Value |
|---|---|
| `timestamp` | ISO-8601 timestamp string. |
| `kind` | The string `crashReport`. |
| `description` | Textual description of the crash or runtime failure. |
| `faultAddress` | Fault address associated with the crash. |
| `platform` | Platform string; first token names the platform, followed by version info — for example, `"linux (Ubuntu 22.04.5 LTS)"`. |
| `architecture` | Processor architecture name. |
| `threads` | Array of thread records. |

The following fields appear conditionally, depending on backtracer settings:

| Field | Value |
|---|---|
| `omittedThreads` | Count of threads omitted when `threads=crashed`. Omitted if zero. |
| `capturedMemory` | Dictionary of captured memory contents, keyed by hex address strings. Absent when `sanitize` is enabled or no data was captured. |
| `omittedImages` | When `images=mentioned`, count of images whose details were omitted. |
| `images` | Array of image records (unless `images=none`). |
| `backtraceTime` | Time taken to generate the report, in seconds. |

#### Thread records

| Field | Value |
|---|---|
| `name` | Thread name; omitted if not set. |
| `crashed` | `true` for the crashing thread; omitted otherwise. |
| `registers` | Dictionary of register name → hex value string. The backtracer omits this on non-crashed threads when `registers=crashed`. |
| `frames` | Array of frame records. |

#### Frame records

Each frame has a `kind`:

| Kind | Meaning |
|---|---|
| `programCounter` | Frame address is a directly captured program counter. |
| `returnAddress` | Frame address is a return address. |
| `asyncResumePoint` | Frame address is a resumption point in an `async` function. |
| `omittedFrames` | Frame-omission record (carries a `count` field). |
| `truncated` | Backtrace was truncated at this point. |

Address-bearing frames also include `address` (hex string).
Symbolicated frames can add `inlined`, `runtimeFailure`, `thunk`, or
`system` Boolean markers, plus the following fields when symbol lookup succeeds:

| Field | Value |
|---|---|
| `symbol` | Mangled symbol name. |
| `offset` | Offset from the symbol to the frame address. |
| `description` | Demangled, human-readable description (when `demangle=yes`). |
| `image` | Name of the image containing the symbol. |
| `sourceLocation` | `{ file, line, column }` dictionary, when `symbolicate=full` and DWARF info is available. |

#### Image records

| Field | Value |
|---|---|
| `name` | Image name. |
| `buildId` | Build ID as un-prefixed hex string. |
| `path` | Path to the image. |
| `baseAddress` | Base address of the image text (hex string). |
| `endOfText` | End of the image text (hex string). |

#### Example payload

The following report comes from a small service whose `MyService.run()` calls
into a synchronous closure that invokes `MyService.handleRequest`, which
dereferences a NULL-adjacent pointer.
The binary was built with debug information and run on Linux arm64 with
default backtracer settings (`format=json`, preset `auto`, `demangle=yes`,
`symbolicate=full`).
For length, the `registers` and `capturedMemory` objects show only a
representative subset of their entries; a real trace contains every
general-purpose register and many more captured memory snapshots.

```json
{
  "timestamp": "2026-05-21T23:51:47.905791Z",
  "kind": "crashReport",
  "description": "Bad pointer dereference",
  "faultAddress": "0x0000000000000004",
  "platform": "Linux (Ubuntu 24.04.4 LTS)",
  "architecture": "arm64",
  "threads": [
    {
      "crashed": true,
      "registers": {
        "x0": "0x0000fffff702f830",
        "x9": "0x0000000000000004",
        "fp": "0x0000fffff702e5f0",
        "lr": "0x0000aaaaaaaa11a8",
        "sp": "0x0000fffff702e5a0",
        "pc": "0x0000aaaaaaaa1804"
      },
      "frames": [
        {
          "kind": "programCounter",
          "address": "0x0000aaaaaaaa1804",
          "symbol": "$s9myservice9MyServiceV13handleRequestyAA8ResponseVAA0E0VF",
          "offset": 180,
          "description": "MyService.handleRequest(_:) + 180",
          "image": "myservice",
          "sourceLocation": {
            "file": "/work/Sources/myservice/Service.swift",
            "line": 7,
            "column": 21
          }
        },
        {
          "kind": "returnAddress",
          "address": "0x0000aaaaaaaa11a8",
          "symbol": "$s9myservice9MyServiceV3runyyYaKFyycfU_",
          "offset": 15,
          "description": "closure #1 in MyService.run() + 15",
          "image": "myservice",
          "sourceLocation": {
            "file": "/work/Sources/myservice/Service.swift",
            "line": 13,
            "column": 17
          }
        },
        {
          "kind": "returnAddress",
          "address": "0x0000aaaaaaaa117c",
          "symbol": "$s9myservice9MyServiceV3runyyYaKFTY0_",
          "offset": 27,
          "description": "MyService.run() + 27",
          "image": "myservice",
          "sourceLocation": {
            "file": "/work/Sources/myservice/Service.swift",
            "line": 15,
            "column": 9
          }
        },
        {
          "kind": "asyncResumePoint",
          "address": "0x0000aaaaaaaa1218",
          "symbol": "$s9myservice4MainV4mainyyYaKFZTQ1_",
          "offset": 0,
          "description": "static Main.main()",
          "image": "myservice",
          "sourceLocation": {
            "file": "/work/Sources/myservice/Service.swift",
            "line": 22,
            "column": 0
          }
        },
        {
          "kind": "asyncResumePoint",
          "address": "0x0000aaaaaaaa1344",
          "system": true,
          "symbol": "$s9myservice4MainV5$mainyyYaKFZTQ0_",
          "offset": 0,
          "description": "static Main.$main()",
          "image": "myservice",
          "sourceLocation": {
            "file": "//<compiler-generated>",
            "line": 0,
            "column": 0
          }
        },
        {
          "kind": "asyncResumePoint",
          "address": "0x0000aaaaaaaa15d8",
          "thunk": true,
          "symbol": "$sIetH_yts5Error_pIegHrzo_TRTQ0_",
          "offset": 0,
          "description": "thunk for @escaping @convention(thin) @async () -> ()",
          "image": "myservice",
          "sourceLocation": {
            "file": "//<compiler-generated>",
            "line": 0,
            "column": 0
          }
        },
        {
          "kind": "asyncResumePoint",
          "address": "0x0000fffff78a4a18",
          "system": true,
          "symbol": "_ZL23completeTaskWithClosurePN5swift12AsyncContextEPNS_10SwiftErrorE",
          "offset": 0,
          "description": "completeTaskWithClosure(swift::AsyncContext*, swift::SwiftError*)",
          "image": "libswift_Concurrency.so"
        }
      ]
    }
  ],
  "capturedMemory": {
    "0x0000aaaaaaaa1804": "280100f9d1ffff97fd7b45a9ff830191",
    "0x0000aaaaaaaa11a8": "fd7bc1a8c0035fd6ff8300d1fd7b01a9",
    "0x0000fffff702e5f0": "00e602f7ffff0000a811aaaaaaaa0000",
    "0x0000fffff702e5a0": "f81410f7ffff00005fe602f7ffff0000"
  },
  "omittedImages": 12,
  "images": [
    {
      "name": "myservice",
      "buildId": "06b835b6531c5086c472cc7c15b89c97ea973e71",
      "path": "/work/.build/aarch64-unknown-linux-gnu/debug/myservice",
      "baseAddress": "0x0000aaaaaaaa0000",
      "endOfText": "0x0000aaaaaaaa70e8"
    },
    {
      "name": "libswift_Concurrency.so",
      "buildId": "76c30ca7c36aab49fe44cfdf7917c95cb33dfa01",
      "path": "/usr/lib/swift/linux/libswift_Concurrency.so",
      "baseAddress": "0x0000fffff7840000",
      "endOfText": "0x0000fffff78bdc48"
    }
  ],
  "backtraceTime": 0.0019300830000000002
}
```

A few things to notice in this trace:

- The async boundary in this program sits between `Main.main()` and
  `MyService.run()` — that's where the `asyncResumePoint` frames begin.
  `MyService.run()` itself appears as a `returnAddress` because at the moment
  of the crash it was running on a regular stack inside the synchronous
  closure it dispatched.
- The frames marked `system: true` (`Main.$main()` and
  `completeTaskWithClosure`) come from compiler-generated entry-point glue
  and the Swift Concurrency runtime. They don't usually have a meaningful
  source location, so the backtracer reports `<compiler-generated>`.
- The frame marked `thunk: true` is a compiler-generated bridge that adapts
  one async calling convention to another.
- `omittedImages: 12` means twelve loaded shared libraries weren't included
  because no captured frame referenced them — the default `images=mentioned`
  keeps the report compact. Set `images=all` to include every loaded image.

### See also

The packaging guide shows the recommended copy location for container images;
see <doc:packaging#Include-the-backtracer-in-the-runtime-image>.
To diagnose backtraces that don't appear or that are missing information,
see <doc:debugging-a-service-using-a-backtrace>.
