# Omit label when first argument forms a grammatical phrase

Otherwise, if the first argument forms part of a grammatical phrase, omit its label, appending any preceding words to the base name.

## Overview

For example: `x.addSubview(y)`.

This guideline implies that if the first argument *doesn't* form
part of a grammatical phrase, it should have a label.

✅ Each first argument reads as part of a grammatical phrase with the base name, so it can safely omit its label:

```swift
view.dismiss(**animated:** false)
let text = words.split(**maxSplits:** 12)
let studentsByName = students.sorted(**isOrderedBefore:** Student.namePrecedes)
```

⛔ Note that it's important that the phrase convey the correct meaning.
The following would be grammatical but would express the wrong
thing.

```swift
view.dismiss(false)   // Don't dismiss? Dismiss a Bool?
words.split(12)       // Split the number 12?
```

Note also that arguments with default values can be omitted, and
in that case do not form part of a grammatical phrase, so they
should always have labels.
