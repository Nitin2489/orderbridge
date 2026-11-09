# Fonts

Self-hosted subsets of [Inter](https://github.com/rsms/inter) (variable,
weights 100–900), licensed under the SIL Open Font License 1.1
(https://openfontlicense.org).

- `inter-latin-var.woff2`: the Latin subset, as previously served by
  `next/font/google` (`subsets: ["latin"]`).
- `inter-rupee-var.woff2`: only the ₹ sign (U+20B9), cut with fonttools'
  `pyftsubset` from Inter's Latin Extended subset.

Why: every price shows ₹, which Google files under Latin Extended, so each
price page also downloaded the 85 KB Latin Extended file for one glyph. The
₹ file is about 1 KB. Other scripts (for example Devanagari in captured
messages) use the system font, as before.
