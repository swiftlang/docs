# Prefer methods and properties to free functions

Prefer methods and properties to free functions.

## Overview

Free functions are used only in special cases:

1. When there's no obvious `self`:

   ```
   min(x, y, z)
   ```

2. When the function is an unconstrained generic:

   ```
   print(x)
   ```

3. When function syntax is part of the established domain notation:

   ```
   sin(x)
   ```
