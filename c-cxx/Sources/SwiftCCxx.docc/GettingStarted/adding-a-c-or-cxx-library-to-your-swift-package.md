# Adding a C or C++ library to your Swift package

Create a Swift package target from a C or C++ source library to import it into Swift.

@Options(scope: local) {
    @AutomaticSeeAlso(disabled)
}

## Overview

Swift provides built-in support for C and C++ code, letting you use existing libraries in ways that feel like native Swift code. The first steps to using C/C++ from your Swift code are to put the library in its own target of your Swift package, compile the library's sources, and generate a module for its public header. After you have a module for the C/C++ library, you can import it from Swift.

This article adds a small sample C library named `my-lib` to a Swift package as a new target. The last section shows what changes for a C++ library.

By convention, the target that wraps a C or C++ library starts with `C` and uses Swift naming conventions; for example, `my-lib` becomes `CMyLib`.

### Create the library and add to your package

This sample library shows some basic configuration options for Swift targets implemented in C. Create a `my-lib` folder for the library and two subdirectories, `src` and `tests`.

`src/addition.h` declares the interface for the library.
```c
#ifndef ADDITION_H
#define ADDITION_H

/// Returns the language the library is implemented in.
const char *language(void);

/// Returns the sum of two integers.
int add(int a, int b);

#endif
```

`src/config.h` defines configuration values.
```c
#define MYLIB_LANGUAGE "C"
```

`src/addition.c` implements the library.
```c
#include "addition.h"
#include "config.h"

const char *language(void) { return MYLIB_LANGUAGE; }

int add(int a, int b) { return a + b; }
```

`my-lib` also ships a standalone test file, `tests/test.c`, which shouldn't be compiled into the Swift target.

The full library looks like this:
```
my-lib
├── src
│   ├── addition.c
│   ├── addition.h
│   └── config.h
└── tests
    └── test.c
```

Create a `CMyLib` folder in the `Sources` directory of your Swift package and add the C library.

```
MyApp
├── Package.swift
└── Sources
    ├── CMyLib
    │   └── my-lib
    │       └── ...
    └── MyApp
        └── MyApp.swift
```

### Create a target and choose which files to compile

Add a new target to your Swift package for the C library. By default, Swift Package Manager (SwiftPM) compiles every C source file under a target's directory. A library you copy in can include files you don't want, such as `tests/test.c` in `my-lib`. Use the `sources` and `exclude` package description properties to define which library files to include. Each entry is a file or a directory, relative to the target's directory.

For `my-lib`, you only want to include the source files in the Swift target. 

```swift
.target(
    name: "CMyLib",
    sources: ["my-lib/src"],
)
```

To configure build settings for a C library target, add a `cSettings` property to the target. For more details on defining C build settings, see [Using build configurations](https://docs.swift.org/latest/documentation/packagemanagerdocs/usingbuildconfigurations).

### Make the library's headers visible to Swift

The Swift compiler uses a module map file to know which C headers to expose to Swift. SwiftPM generates a module map for the target based on the contents of the `include` directory, unless one already exists there. 

Add public header files for `my-lib` to the `include` directory and create `CMyLib.h` as an umbrella header:

```c
#ifndef CMYLIB_H
#define CMYLIB_H

#include "addition.h"
#include "config.h"

#endif
```

Your file structure now looks like this:
```
MyApp
├── Package.swift
└── Sources
    ├── CMyLib
    │   ├── include
    │   │   └── CMyLib.h
    │   └── my-lib
    │       ├── src
    │       │   ├── addition.c
    │       │   ├── addition.h
    │       │   └── config.h
    │       └── tests
    │           └── test.c
    └── MyApp
        └── MyApp.swift
```

For more information on module map generation, see [Creating Module Maps](https://docs.swift.org/latest/documentation/packagemanagerdocs/modulemaps).

### Import the library from Swift

Import the module by the target's name. Everything declared in the public header is available in Swift code.

```swift
// Sources/MyApp/MyApp.swift
import CMyLib

print(String(cString: CMyLib.language()), CMyLib.add(1, 2))
```

Build and run the package. The program prints the constant and the value the C library calculates:

```bash
$ swift run
C 3
```

### Use a C++ library

For a C++ library, you use different target settings and enable C++ interoperability on the Swift target.

The C++ example library has a similar structure to the C library to show similarities and differences. Rename the `CMyLib` folder to `CxxLib`. Rename the `my-lib` folder to `cxx-lib`.

Rename `addition.h` to `subtraction.hpp` and change the interface of the library to:
```cpp
#ifndef SUBTRACTION_H
#define SUBTRACTION_H

namespace cxxlib {

/// Returns the language the library is implemented in.
const char *language(void);

/// Returns the difference of two integers.
int subtract(int a, int b);

}

#endif
```

Change the value in `config.h` to `C++`.
```c
#define MYLIB_LANGUAGE "C++"
```

Rename `addition.c` to `subtraction.cpp` and change the implementation to:
```cpp
#include "subtraction.hpp"
#include "config.h"

namespace cxxlib {

const char *language(void) { return MYLIB_LANGUAGE; }

int subtract(int a, int b) { return a - b; }

}
```


Update the target in `Package.swift`:
```swift
.target(
    name: "CxxLib",
    sources: ["cxx-lib/src"],
),
```

Turn on C++ interoperability for the Swift target that imports the library by adding `.interoperabilityMode(.Cxx)` to the `swiftSettings` property.
```swift
.executableTarget(
    name: "MyApp",
    dependencies: ["CxxLib"],
    swiftSettings: [
        .interoperabilityMode(.Cxx)
    ],
),
```

> Note: Configure build settings for a C++ library using the `cxxSettings` target property. Set the package property `cxxLanguageStandard` to define the C++ language standard to use for all C++ targets in this package.

Import the C++ module using the target's name and use the C++ namespace the library defines.

```swift
import CxxLib

print(String(cString: cxxlib.language()), cxxlib.subtract(1, 2))
```

Build and run the package. The program prints the value the C++ library calculates:

```bash
$ swift run
C++ -1
```

For a more detailed walkthrough of exposing a C++ class to Swift — including Swift Package Manager, command-line, and Xcode workflows — see <doc:enabling-c++-interoperability-in-your-project>.

