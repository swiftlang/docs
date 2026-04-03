# ``APIGuidelines``

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

- <doc:API-0001>

### Naming — Promote Clear Usage

- <doc:API-0002>
- <doc:API-0003>
- <doc:API-0004>
- <doc:API-0005>

### Naming — Strive for Fluent Usage

- <doc:API-0006>
- <doc:API-0007>
- <doc:API-0008>
- <doc:API-0009>
- <doc:API-0010>
- <doc:API-0011>
- <doc:API-0012>
- <doc:API-0013>

### Naming — Use Terminology Well

- <doc:API-0014>
- <doc:API-0015>
- <doc:API-0016>
- <doc:API-0017>

### Conventions — General

- <doc:API-0018>
- <doc:API-0019>
- <doc:API-0020>
- <doc:API-0021>

### Conventions — Parameters

- <doc:API-0022>
- <doc:API-0023>
- <doc:API-0024>
- <doc:API-0025>

### Conventions — Argument Labels

- <doc:API-0026>
- <doc:API-0027>
- <doc:API-0028>
- <doc:API-0029>
- <doc:API-0030>

### Special Instructions

- <doc:API-0031>
- <doc:API-0032>
