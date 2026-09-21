# Label prepositional phrase arguments

When the first argument forms part of a prepositional phrase, give it an argument label.

## Overview

The argument label should normally begin at the preposition. For example: `x.removeBoxes(havingLength: 12)`.

For more on prepositional phrases, see [prepositional phrase](https://en.wikipedia.org/wiki/Adpositional_phrase#Prepositional_phrases). For more on prepositions, see [preposition](https://en.wikipedia.org/wiki/Preposition).

An exception arises when the first two arguments represent parts of
a single abstraction.

⛔ Splitting the label across both arguments breaks the single abstraction into two disconnected phrases:

```swift
a.move(**toX:** b, **y:** c)
a.fade(**fromRed:** b, **green:** c, **blue:** d)
```

✅ In such cases, begin the argument label *after* the preposition, to
keep the abstraction clear.

```swift
a.moveTo(**x:** b, **y:** c)
a.fadeFrom(**red:** b, **green:** c, **blue:** d)
```
