# Omit labels for indistinguishable arguments

Omit all labels when arguments can't be usefully distinguished.

## Overview

For example: `min(number1, number2)`, `zip(sequence1, sequence2)`.

Argument labels appear at a function or method's point of use:

```swift
func move(**from** start: Point, **to** end: Point)
x.move(**from:** x, **to:** y)
```
