# ``APIGuidelines``

Design Swift APIs that prioritize clarity at the point of use through effective naming and consistent conventions.

@Metadata {
    @DisplayName("API Guidelines")
}

## Overview

Delivering a clear, consistent developer experience when writing Swift code is largely defined by the names and idioms that appear in APIs.
These design guidelines explain how to make sure that your code feels like a part of the larger Swift ecosystem.

* **Clarity at the point of use** is your most important goal.
  Entities such as methods and properties are declared only once but
  *used* repeatedly.  Design APIs to make those uses clear and
  concise.  When evaluating a design, reading a declaration is seldom
  sufficient; always examine a use case to make sure it looks
  clear in context.

* **Clarity is more important than brevity.**  Although Swift
  code can be compact, it is a *non-goal*
  to enable the smallest possible code with the fewest characters.
  Brevity in Swift code, where it occurs, is a side-effect of the
  strong type system and features that naturally reduce boilerplate.

* **Write a documentation comment**
  for every declaration. Insights gained by writing documentation can
  have a profound impact on your design, so don't put it off.

> Warning:
> If you are having trouble describing your API's
> functionality in simple terms, **you may have designed the wrong API.**

## Topics

### Fundamentals

- <doc:documentation-comments>

### Naming — Promote Clear Usage

- <doc:avoid-ambiguity>
- <doc:omit-needless-words>
- <doc:name-according-to-roles>
- <doc:weak-type-information>

### Naming — Strive for Fluent Usage

- <doc:grammatical-phrases>
- <doc:factory-methods>
- <doc:initializer-first-arguments>
- <doc:side-effect-naming>
- <doc:boolean-assertions>
- <doc:protocol-nouns>
- <doc:protocol-capability-suffixes>
- <doc:noun-names>

### Naming — Use Terminology Well

- <doc:avoid-obscure-terms>
- <doc:established-meaning>
- <doc:avoid-abbreviations>
- <doc:embrace-precedent>

### Conventions — General

- <doc:computed-property-complexity>
- <doc:methods-over-free-functions>
- <doc:case-conventions>
- <doc:shared-base-names>

### Conventions — Parameters

- <doc:parameter-names-for-documentation>
- <doc:defaulted-parameters>
- <doc:default-parameter-order>
- <doc:prefer-fileid>

### Conventions — Argument Labels

- <doc:indistinguishable-arguments>
- <doc:value-preserving-conversions>
- <doc:prepositional-phrase-labels>
- <doc:grammatical-phrase-arguments>
- <doc:label-other-arguments>

### Special Instructions

- <doc:tuple-and-closure-labels>
- <doc:unconstrained-polymorphism>
