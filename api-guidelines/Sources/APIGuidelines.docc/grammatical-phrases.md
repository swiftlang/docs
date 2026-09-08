# Prefer grammatical English phrases

Prefer method and function names that make use sites form grammatical English phrases.

## Overview

✅ Each of these reads as a grammatical English phrase at the call site:

```swift
x.insert(y, at: z)          // "x, insert y at z"
x.subviews(havingColor: y)  // "x's subviews having color y"
x.capitalizingNouns()       // "x, capitalizing nouns"
```

⛔ Each of these drops the words that make the phrase grammatical, so the call no longer reads as English:

```swift
x.insert(y, position: z)
x.subviews(color: y)
x.nounCapitalize()
```

It is acceptable for fluency to degrade after the first argument or
two when those arguments are not central to the call's meaning:

```swift
AudioUnit.instantiate(
  with: description,
  **options: [.inProcess], completionHandler: stopProgressBar**)
```
