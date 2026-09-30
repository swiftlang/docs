# Choose parameter names to serve documentation

Choose parameter names to serve documentation.

## Overview

Even though parameter names do not appear at a function or method's point of use, they play an important explanatory role.

Parameter names appear in a function or method's declaration:

```swift
func move(from **start**: Point, to **end**: Point)
```

Choose these names to make documentation easy to read.

✅ For example, these names make documentation read naturally:

```swift
/// Return an `Array` containing the elements of `self`
/// that satisfy `**predicate**`.
func filter(_ **predicate**: (Element) -> Bool) -> [Generator.Element]

/// Replace the given `**subRange**` of elements with `**newElements**`.
mutating func replaceRange(_ **subRange**: Range<Index>, with **newElements**: [E])
```

⛔ These, however, make the documentation awkward and ungrammatical:

```swift
/// Return an `Array` containing the elements of `self`
/// that satisfy `**includedInResult**`.
func filter(_ **includedInResult**: (Element) -> Bool) -> [Generator.Element]

/// Replace the **range of elements indicated by `r`** with
/// the contents of `**with**`.
mutating func replaceRange(_ **r**: Range<Index>, **with**: [E])
```
